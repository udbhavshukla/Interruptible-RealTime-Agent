"""Step 9: end-to-end interruptible runtime demo (deterministic, in-process).

Drives the *real* runtime — Coordinator, mailbox, registry, supervisor,
cancellation manager, acceptance gate — through the canonical AURA scenario.
No LLM, no planner, no network, no audio, no frontend: one injectable mock
tool and asyncio gates make it fully deterministic.

Scenario timeline::

    T0   USER: "Find flight Bangalore to Delhi"
    T1   Coordinator creates version 1
    T2   call_001 starts (tagged spawn_version=1)
    T3   USER INTERRUPTS: "Actually Mumbai"
    T4   interruption processed (single-writer handler)
    T5   call_001 cancellation requested (advisory)
    T6   state changes v1 -> v2 (version minted BEFORE new work)
    T7   call_002 starts (tagged spawn_version=2)
    T8   call_001 returns late — it ignored the cancellation
    T9   Acceptance Gate rejects call_001 as STALE
    T10  call_002 completes
    T11  Acceptance Gate accepts call_002
    T12  final intent: Bangalore -> Mumbai

The mock tool (:class:`DemoFlightTool`) plays the "flight API":

- the obsolete Delhi search blocks until cancelled, **ignores** the
  cancellation, and returns a late Delhi quote — the exact case the
  runtime must survive (cancellation is advisory);
- the current Mumbai search waits for :meth:`DemoFlightTool.release`
  so the demo controls exactly when it completes.

The readable trace is derived from runtime facts only: the consumed
mailbox events, the Coordinator's outbox, and gate-accepted results.
Every wait is an ``asyncio.Event`` gate or a ``wait_for(timeout)`` guard —
no ``sleep``, so two runs produce byte-identical traces.

Run it::

    python -m aura.runtime.demo
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .coordinator import Coordinator
from .events import Event, EventType

__all__ = [
    "run_demo",
    "main",
    "DemoReport",
    "DemoFlightTool",
    "FlightQuote",
    "Trace",
    "SESSION_ID",
    "INTENT_DELHI",
    "INTENT_MUMBAI",
    "FLIGHT_TO_DELHI",
    "FLIGHT_TO_MUMBAI",
    "DEFAULT_TIMEOUT",
]

SESSION_ID = "demo-session"
INTENT_DELHI = "Find flight Bangalore to Delhi"
INTENT_MUMBAI = "Actually Mumbai"
DEFAULT_TIMEOUT = 2.0  # wait_for guard only — gates decide, never the clock


@dataclass(frozen=True)
class FlightQuote:
    """A canned flight result (the mock "API" response)."""

    origin: str
    destination: str
    price_inr: int

    @property
    def route(self) -> str:
        return f"{self.origin} → {self.destination}"


#: What the obsolete v1 search returns — late, and therefore stale.
FLIGHT_TO_DELHI = FlightQuote(origin="Bangalore", destination="Delhi", price_inr=6399)
#: What the current v2 search returns — the answer the user keeps.
FLIGHT_TO_MUMBAI = FlightQuote(origin="Bangalore", destination="Mumbai", price_inr=4299)


class DemoFlightTool:
    """Deterministic stand-in for a flight-search API (never touches network).

    The Coordinator calls it as ``tool(intent=..., call_id=..., state=...)``.
    Each call exposes a ``started`` event (set when its body begins) so the
    driver can sequence phases without sleeping. ``state`` is part of the
    call contract; this tool ignores it (the quote depends only on the intent).
    """

    def __init__(self) -> None:
        self.started: Dict[str, asyncio.Event] = {}
        self._release = asyncio.Event()

    def release(self) -> None:
        """Let the waiting (current) search return its result."""
        self._release.set()

    async def __call__(self, *, intent: str, call_id: str, state: Any) -> FlightQuote:
        # setdefault: a driver may pre-register the gate before the task's
        # first scheduling; the body marks it when it actually begins.
        began = self.started.setdefault(call_id, asyncio.Event())
        began.set()

        if "mumbai" in intent.lower():
            # Current work: finishes only when the demo releases it.
            await self._release.wait()
            return FLIGHT_TO_MUMBAI

        # Obsolete work: it never finishes on its own, and it *ignores*
        # cancellation — only the cancel prods it into returning its
        # (now stale) Delhi quote. T8/T9 depend on exactly this.
        try:
            await asyncio.Event().wait()  # ends only via cancellation
        except asyncio.CancelledError:
            pass
        return FLIGHT_TO_DELHI


class Trace:
    """Ordered, printable record of the demo's presentation lines."""

    def __init__(self) -> None:
        self._lines: List[str] = []

    def add(self, line: str) -> None:
        self._lines.append(line)

    def add_blank(self) -> None:
        """Separate phases; never leading, never doubled."""
        if self._lines and self._lines[-1] != "":
            self._lines.append("")

    @property
    def lines(self) -> Tuple[str, ...]:
        return tuple(self._lines)


class _TraceRenderer:
    """Turns runtime facts into the presentation trace.

    For every consumed mailbox event, renders in order:

    1. the consumed event itself — what happened,
    2. every *new* event from the Coordinator's outbox — what it decided,
    3. any newly gate-accepted result — the verdict that stuck.
    """

    def __init__(self, coordinator: Coordinator, trace: Trace) -> None:
        self._coordinator = coordinator
        self._trace = trace
        self._rendered_emitted = 0
        self._accepted_seen = set()

    def consume(self, event: Event) -> None:
        self._render_input(event)
        self._render_outbox()
        self._render_acceptances()

    # -------------------------------------------------------------- input
    def _render_input(self, event: Event) -> None:
        etype = event.event_type
        if etype in (EventType.USER_INPUT, EventType.USER_INTERRUPT):
            intent = self._intent_of(event)
            if intent is None:
                return
            self._trace.add_blank()
            self._trace.add(f"[USER] {intent}")
            if etype is EventType.USER_INTERRUPT:
                self._trace.add("[COORDINATOR] Interrupt detected")
            return
        if etype in (
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
            EventType.TASK_CANCELLED,
        ):
            call_id = event.payload.get("call_id", "?")
            self._trace.add_blank()
            if etype is not EventType.TASK_COMPLETED:
                self._trace.add(f"[TOOL] {call_id} {etype.value.split('.')[-1]}")
                return
            spawn_version = event.payload.get("spawn_version")
            if spawn_version is not None and spawn_version < (
                self._coordinator.current_version
            ):
                self._trace.add(f"[TOOL] {call_id} returned late")
            else:
                self._trace.add(f"[TOOL] {call_id} completed")
            return
        if etype is EventType.RUNTIME_ERROR:
            self._trace.add_blank()
            self._trace.add(f"[ERROR] {event.payload.get('error', 'unknown')}")

    @staticmethod
    def _intent_of(event: Event) -> Optional[str]:
        for key in ("intent", "text"):
            value = event.payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    # ------------------------------------------------------------- outbox
    def _render_outbox(self) -> None:
        emitted = self._coordinator.emitted
        for new_event in emitted[self._rendered_emitted :]:
            self._render_emitted(new_event)
        self._rendered_emitted = len(emitted)

    def _render_emitted(self, event: Event) -> None:
        payload = event.payload
        etype = event.event_type
        if etype is EventType.STATE_VERSION_CHANGED:
            frm, to = payload["from"], payload["to"]
            if frm == 0:
                self._trace.add(f"[STATE] version={to}")
            else:
                self._trace.add(f"[STATE] v{frm} → v{to}")
        elif etype is EventType.TASK_CANCEL_REQUESTED:
            self._trace.add(
                f"[CANCEL] {payload['call_id']} cancellation requested"
            )
        elif etype is EventType.TASK_STARTED:
            self._trace.add(f"[TOOL] {payload['call_id']} started")
        elif etype is EventType.TASK_RESULT_REJECTED:
            decision = str(payload.get("decision", "rejected")).upper()
            self._trace.add(
                f"[ACCEPTANCE] {payload['call_id']} → {decision}_REJECTED"
            )
        elif etype is EventType.RUNTIME_ERROR:
            self._trace.add(f"[ERROR] {payload.get('error', 'unknown')}")

    # --------------------------------------------------------- acceptance
    def _render_acceptances(self) -> None:
        for call_id in self._coordinator.accepted_results:
            if call_id not in self._accepted_seen:
                self._accepted_seen.add(call_id)
                self._trace.add(f"[ACCEPTANCE] {call_id} → ACCEPTED")


@dataclass(frozen=True)
class DemoReport:
    """The demo's outcome: readable trace plus the runtime facts behind it."""

    trace: Tuple[str, ...]
    coordinator: Coordinator
    final_result: Optional[FlightQuote] = None

    @property
    def text(self) -> str:
        return "\n".join(self.trace)

    @property
    def final_version(self) -> int:
        return self.coordinator.current_version

    @property
    def final_intent(self) -> Optional[str]:
        state = self.coordinator.state
        return state.intent if state is not None else None

    @property
    def final_route(self) -> Optional[str]:
        return self.final_result.route if self.final_result is not None else None


def _last_quote(accepted: Dict[str, Any]) -> Optional[FlightQuote]:
    for value in reversed(list(accepted.values())):
        if isinstance(value, FlightQuote):
            return value
    return None


async def run_demo(*, timeout: float = DEFAULT_TIMEOUT) -> DemoReport:
    """Run the full T0-T12 scenario in-process and return its report.

    Deterministic: phases advance on tool gates (``started``/``release``)
    and completed supervised tasks; ``timeout`` only guards against hangs.
    """
    tool = DemoFlightTool()
    coordinator = Coordinator(SESSION_ID, tool=tool, tool_name="mock_flight_search")
    trace = Trace()
    renderer = _TraceRenderer(coordinator, trace)

    async def consume() -> None:
        event = await asyncio.wait_for(coordinator.process_next(), timeout)
        renderer.consume(event)

    async def say(event_type: EventType, intent: str) -> None:
        """Produce a user event, then let the Coordinator consume it (I2)."""
        await asyncio.wait_for(
            coordinator.mailbox.put(
                Event(
                    session_id=SESSION_ID,
                    event_type=event_type,
                    payload={"intent": intent},
                    actor="user",
                )
            ),
            timeout,
        )
        await consume()

    # T0-T2: the original request -> version 1, call_001.
    # Gates are pre-registered so no phase ever depends on task scheduling
    # order — the demo sequences on events only.
    first_search = tool.started.setdefault("call_001", asyncio.Event())
    second_search = tool.started.setdefault("call_002", asyncio.Event())

    await say(EventType.USER_INPUT, INTENT_DELHI)
    await asyncio.wait_for(first_search.wait(), timeout)

    # T3-T7: interruption -> v2, call_001 cancel requested, call_002 spawned.
    await say(EventType.USER_INTERRUPT, INTENT_MUMBAI)
    await asyncio.wait_for(second_search.wait(), timeout)

    # T8-T9: call_001 ignores the cancel and returns late -> gate says STALE.
    await asyncio.wait_for(coordinator.supervisor.task_for("call_001"), timeout)
    await consume()

    # T10-T11: call_002 completes -> gate accepts it.
    tool.release()
    await asyncio.wait_for(coordinator.supervisor.task_for("call_002"), timeout)
    await consume()

    # T12: final line, straight from the accepted result.
    final_result = _last_quote(coordinator.accepted_results)
    trace.add_blank()
    if final_result is not None:
        trace.add(f"[FINAL] {final_result.route}")
    elif coordinator.state is not None and coordinator.state.intent:
        trace.add(f"[FINAL] {coordinator.state.intent}")
    else:
        trace.add("[FINAL] (no accepted result)")

    return DemoReport(
        trace=trace.lines, coordinator=coordinator, final_result=final_result
    )


def main() -> None:
    """Console entrypoint: run the demo and print its trace.

    Reconfigures stdout to UTF-8 when possible so the ``→`` arrows survive
    legacy Windows code pages (cp1252); a live demo must never crash while
    printing. Under ``redirect_stdout`` (tests) this step is skipped.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # closed/redirected stream
            pass
    report = asyncio.run(run_demo())
    print(report.text)


if __name__ == "__main__":
    main()
