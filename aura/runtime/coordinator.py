"""Runtime coordinator for the AURA real-time runtime (Step 8).

The Coordinator is the **single writer** of one session's runtime state::

    producers --> SessionMailbox --> Coordinator.process_next()/run()
                                        |-- SessionState     (versioned, I3)
                                        |-- TaskSupervisor   (async work)
                                        |-- CancellationManager (advisory, I4)
                                        |-- AcceptanceGate   (authoritative, I4)
                                        +-- outbox of runtime events

Invariant mapping
-----------------
- **I1 single writer** : session state, the active-task map, and accepted
  results change *only* inside Coordinator handlers. Everything else feeds
  it events.
- **I2 total order** : one consumer; events are consumed from the mailbox in
  ``seq`` order (CONTROL before DATA), one at a time. Handlers never await
  I/O, so no transition interleaves. A second concurrent consumer is
  rejected with ``RuntimeError`` (single-consumer guard in
  ``process_next``).
- **I3 version tagging** : a call is spawned from the *current* snapshot and
  the registry stamps ``spawn_version``; the future-work guarantee follows
  because the version is advanced *before* new work is spawned.
- **I4 cancellation advisory** : interruptions request cancellation through
  the CancellationManager (tombstone-first), but stale protection comes from
  the Acceptance Gate when outcomes arrive — never from cancellation.

Event handling
--------------
==================  ==========================================================
Event type          Effect
==================  ==========================================================
``USER_INPUT``      new intent -> new version -> cancel obsolete -> spawn call
``USER_INTERRUPT``  same, but flagged as an interruption (reason recorded)
``TASK_COMPLETED``  outcome arrives -> pop active -> gate -> accept/reject
``TASK_FAILED``     same outcome path as ``TASK_COMPLETED``
``TASK_CANCELLED``  same outcome path as ``TASK_COMPLETED``
anything else       ``RUNTIME_ERROR`` emitted to the outbox (loop never dies)
==================  ==========================================================

Deliberately small: no planner, no real tools (one injectable async
callable), no audio, no network, no persistence. Testable entirely
in-process.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .acceptance import AcceptanceGate, Decision
from .cancellation import CancellationManager
from .events import Event, EventType
from .mailbox import Priority, SessionMailbox
from .registry import TaskRegistry, TaskState
from .state import SessionState
from .supervisor import TaskSupervisor

__all__ = ["Coordinator", "ToolCallable"]

#: Injectable tool: called as ``tool(intent=..., call_id=..., state=...)``
#: and must return an awaitable.
ToolCallable = Callable[..., Awaitable[Any]]

_OUTCOME_EVENTS = {
    TaskState.SUCCEEDED: EventType.TASK_COMPLETED,
    TaskState.FAILED: EventType.TASK_FAILED,
    TaskState.CANCELLED: EventType.TASK_CANCELLED,
}


class Coordinator:
    """Single-writer event consumer for one session."""

    def __init__(
        self,
        session_id: str,
        *,
        tool: ToolCallable,
        tool_name: str = "tool",
        maxsize: int = 1000,
    ) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if not callable(tool):
            raise TypeError(f"tool must be callable, got {type(tool).__name__}")

        self._session_id = session_id
        self._tool = tool
        self._tool_name = tool_name

        # Runtime components (the Coordinator is their only driver).
        self._mailbox = SessionMailbox(session_id, maxsize=maxsize)
        self._registry = TaskRegistry()
        self._supervisor = TaskSupervisor(self._registry)
        self._canceller = CancellationManager(self._supervisor)
        self._gate = AcceptanceGate(self._registry)

        # Session-level runtime state (I1: mutated only in handlers below).
        self._state: Optional[SessionState] = None
        self._active: Dict[str, int] = {}  # call_id -> spawn_version
        self._call_counter = 0
        self._consuming = False  # I2 guard: at most one process_next() in flight
        self._emitted: List[Event] = []
        self._watchers: List[asyncio.Task] = []
        self.accepted_results: Dict[str, Any] = {}

    # ------------------------------------------------------------- inspection
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def mailbox(self) -> SessionMailbox:
        return self._mailbox

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    @property
    def supervisor(self) -> TaskSupervisor:
        return self._supervisor

    @property
    def gate(self) -> AcceptanceGate:
        return self._gate

    @property
    def state(self) -> Optional[SessionState]:
        """Current session snapshot (``None`` before the first input)."""
        return self._state

    @property
    def current_version(self) -> int:
        return self._state.version if self._state is not None else 0

    @property
    def active_call_ids(self) -> Tuple[str, ...]:
        return tuple(self._active)

    @property
    def emitted(self) -> Tuple[Event, ...]:
        """Runtime events produced for important transitions (outbox)."""
        return tuple(self._emitted)

    # ------------------------------------------------------------ consumption
    async def process_next(self) -> Event:
        """Consume and handle exactly one event, in mailbox order (I2).

        Never raises for handler bugs — they become ``RUNTIME_ERROR`` events
        in the outbox. The mailbox ``task_done()`` bookkeeping is always
        balanced for consumed events.

        Single consumer (I2): only one call may be in flight at a time. A
        second concurrent caller — e.g. ``run()`` running alongside a manual
        ``process_next()`` — gets ``RuntimeError`` instead of silently
        interleaving consumption. The guard is released on every exit path
        (normal return, contained handler error, cancellation).
        """
        if self._consuming:
            raise RuntimeError(
                "coordinator already has an active consumer; "
                "process_next() must not be called concurrently"
            )
        self._consuming = True
        try:
            event = await self._mailbox.get()
            try:
                self._dispatch(event)
            except Exception as exc:  # handler bug must not kill the consumer
                self._emit(
                    EventType.RUNTIME_ERROR,
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "event_type": str(event.event_type),
                    },
                    caused_by=event,
                )
            finally:
                self._mailbox.task_done()
            return event
        finally:
            self._consuming = False

    async def run(self) -> None:
        """Consume events forever (single consumer). Cancels cleanly."""
        while True:
            await self.process_next()

    # -------------------------------------------------------------- dispatch
    def _dispatch(self, event: Event) -> None:
        etype = event.event_type
        if etype is EventType.USER_INPUT:
            self._handle_intent(event, kind="user_input")
        elif etype is EventType.USER_INTERRUPT:
            self._handle_intent(event, kind="user_interrupt")
        elif etype in (
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
            EventType.TASK_CANCELLED,
        ):
            self._handle_task_outcome(event)
        else:
            self._emit(
                EventType.RUNTIME_ERROR,
                {"error": f"unhandled event type {etype}", "event_id": event.event_id},
                caused_by=event,
            )

    def _handle_intent(self, event: Event, *, kind: str) -> None:
        intent = self._intent_of(event)
        if intent is None:
            if kind == "user_interrupt":
                # Pure barge-in with no corrected input: intent unchanged,
                # so nothing is superseded (no version bump, no cancels).
                return
            self._emit(
                EventType.RUNTIME_ERROR,
                {"error": "USER_INPUT requires an 'intent' or 'text' payload"},
                caused_by=event,
            )
            return
        self._supersede(intent, reason=kind, caused_by=event)

    def _supersede(self, intent: str, *, reason: str, caused_by: Event) -> None:
        """Advance the version for a changed intent, then retire old work."""
        if self._state is not None and intent == self._state.intent:
            return  # active intent unchanged -> nothing to supersede

        previous_version = self.current_version
        if self._state is None:
            self._state = SessionState.initial(self._session_id, intent=intent)
        else:
            self._state = self._state.derive(intent=intent)

        # I3: version advances BEFORE any new work is spawned.
        self._emit(
            EventType.STATE_VERSION_CHANGED,
            {
                "from": previous_version,
                "to": self._state.version,
                "intent": intent,
                "reason": reason,
            },
            caused_by=caused_by,
        )

        # I4: cancellation of obsolete work — attempted, advisory.
        for call_id in list(self._active):
            outcome = self._canceller.request(call_id)
            self._emit(
                EventType.TASK_CANCEL_REQUESTED,
                {
                    "call_id": call_id,
                    "outcome": outcome.value,
                    "spawn_version": self._active[call_id],
                },
                caused_by=caused_by,
            )

        self._spawn(intent, caused_by=caused_by)

    def _handle_task_outcome(self, event: Event) -> None:
        call_id = event.payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            self._emit(
                EventType.RUNTIME_ERROR,
                {"error": "task outcome event requires a 'call_id' payload"},
                caused_by=event,
            )
            return

        self._active.pop(call_id, None)

        # I4: the gate — not cancellation — decides. An old result can never
        # mutate current state (it is rejected before anything is written).
        decision = self._gate.evaluate(call_id, self.current_version)
        if decision.decision is Decision.ACCEPTED:
            self.accepted_results[call_id] = self._supervisor.result(call_id)
            return

        self._emit(
            EventType.TASK_RESULT_REJECTED,
            {
                "call_id": call_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "spawn_version": decision.spawn_version,
                "state": decision.state.value if decision.state else None,
            },
            caused_by=event,
        )

    # ----------------------------------------------------------------- spawn
    def _spawn(self, intent: str, *, caused_by: Optional[Event]) -> str:
        snapshot = self._state
        assert snapshot is not None  # callers advance state first

        self._call_counter += 1
        call_id = f"call_{self._call_counter:03d}"
        task = self._supervisor.start(
            call_id,
            lambda: self._tool(intent=intent, call_id=call_id, state=snapshot),
            tool_name=self._tool_name,
            spawn_version=snapshot.version,  # I3: tagged with current version
            plan_id=snapshot.plan_id,
        )
        self._active[call_id] = snapshot.version
        self._watchers.append(asyncio.create_task(self._watch(call_id, task)))
        self._emit(
            EventType.TASK_STARTED,
            {"call_id": call_id, "version": snapshot.version, "intent": intent},
            caused_by=caused_by,
        )
        return call_id

    async def _watch(self, call_id: str, task: asyncio.Task) -> None:
        """Turn a settled task into a mailbox event (keeps I2 ordering)."""
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected for cancelled calls; the record already settled

        record = self._registry.get(call_id)
        etype = _OUTCOME_EVENTS.get(record.state, EventType.TASK_COMPLETED)
        await self._mailbox.put(
            Event(
                session_id=self._session_id,
                event_type=etype,
                payload={
                    "call_id": call_id,
                    "state": record.state.value,
                    "spawn_version": record.spawn_version,
                },
                actor=f"task:{call_id}",
                version=self.current_version,
            ),
            priority=Priority.DATA,
        )

    # ---------------------------------------------------------------- helpers
    def _emit(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        caused_by: Optional[Event] = None,
    ) -> Event:
        correlation_id: Optional[str] = None
        causation_id: Optional[str] = None
        if caused_by is not None:
            causation_id = caused_by.event_id
            correlation_id = caused_by.correlation_id or caused_by.event_id
        event = Event(
            session_id=self._session_id,
            event_type=event_type,
            actor="coordinator",
            payload=payload,
            version=self.current_version,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        self._emitted.append(event)
        return event

    @staticmethod
    def _intent_of(event: Event) -> Optional[str]:
        for key in ("intent", "text"):
            value = event.payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None
