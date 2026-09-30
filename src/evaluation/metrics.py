from __future__ import annotations

from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
# Primary names are the event types actually emitted by the evaluation
# scenarios (scenarios/*.json): USER_INPUT, FAST_ACK, TOOL_CALL,
# TOOL_RESULT, FINAL_RESPONSE, USER_INTERRUPT, CANCEL, STATE_UPDATE,
# STALE_REJECT.
# Every metric default below leads with these names; the legacy lowercase
# spellings are kept *only* as accepted aliases so older callers and the
# pre-existing tests continue to work unchanged - nothing in the project
# depends on them.
INTERRUPTION_EVENTS: tuple[str, ...] = ("USER_INTERRUPT", "interruption")
STALE_RESULT_EVENTS: tuple[str, ...] = ("STALE_REJECT", "stale_result")


def _as_type_tuple(event_type: "str | Sequence[str]") -> tuple[str, ...]:
    """Normalise an event type name (or a group of aliases) to a tuple."""
    if isinstance(event_type, str):
        return (event_type,)
    return tuple(event_type)


# ---------------------------------------------------------------------------
# Helpers for locating events
# ---------------------------------------------------------------------------

def _find_first(events: Sequence[dict], event_type: "str | Sequence[str]") -> tuple[Optional[int], Optional[dict]]:
    """Return (index, event) for the first event of *event_type* or (None, None)."""
    types = _as_type_tuple(event_type)
    for i, ev in enumerate(events):
        if ev.get("type") in types:
            return i, ev
    return None, None


def _find_next_after(events: Sequence[dict], start_index: int, event_type: "str | Sequence[str]") -> tuple[Optional[int], Optional[dict]]:
    """Return (index, event) for the first event of *event_type* after *start_index*."""
    types = _as_type_tuple(event_type)
    for i in range(start_index + 1, len(events)):
        ev = events[i]
        if ev.get("type") in types:
            return i, ev
    return None, None


def _latency(events: Sequence[dict], start_type: "str | Sequence[str]", end_type: "str | Sequence[str]") -> Optional[float]:
    """Compute time (seconds) between the first *start_type* event and the next *end_type* event."""
    start_i, start_ev = _find_first(events, start_type)
    if start_ev is None:
        return None
    end_i, end_ev = _find_next_after(events, start_i, end_type)
    if end_ev is None:
        return None
    try:
        return float(end_ev["timestamp"]) - float(start_ev["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def _count(events: Sequence[dict], event_type: "str | Sequence[str]") -> int:
    """Count events of a given type (or of any type in a group of aliases)."""
    types = _as_type_tuple(event_type)
    return sum(1 for ev in events if ev.get("type") in types)


def _rate(events: Sequence[dict], numerator_type: str, denominator_type: str) -> float:
    """Return *numerator_type* / *denominator_type* (0.0 when denominator is 0)."""
    denominator = _count(events, denominator_type)
    if denominator == 0:
        return 0.0
    return _count(events, numerator_type) / denominator


# ---------------------------------------------------------------------------
# Latency metrics
# ---------------------------------------------------------------------------

def ttfr(events: Sequence[dict],
         start_types: Sequence[str] = ("USER_INPUT", "user_say", "user_input"),
         end_types: Sequence[str] = ("FAST_ACK", "TOOL_RESULT", "FINAL_RESPONSE",
                                     "tool_result", "assistant_response")) -> Optional[float]:
    """Time‑to‑first‑response (seconds).

    Measures the gap between the first user input event (``USER_INPUT``;
    legacy ``user_say`` / ``user_input``) and the first response that
    follows it.  In the project vocabulary a response is the fast
    acknowledgement ``FAST_ACK``, a ``TOOL_RESULT``, or the
    ``FINAL_RESPONSE`` - checked in that priority order, so the earliest
    acknowledgement wins.  Legacy ``tool_result`` /
    ``assistant_response`` remain accepted.
    """
    start_i = start_ev = None
    for st in start_types:
        start_i, start_ev = _find_first(events, st)
        if start_ev is not None:
            break
    if start_ev is None:
        return None

    for et in end_types:
        end_i, end_ev = _find_next_after(events, start_i, et)
        if end_ev is not None:
            try:
                return float(end_ev["timestamp"]) - float(start_ev["timestamp"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def interruption_detection_latency(events: Sequence[dict],
                                   start_type: "str | Sequence[str]" = INTERRUPTION_EVENTS,
                                   end_type: "str | Sequence[str]" = ("CANCEL", "interruption_detected")) -> Optional[float]:
    """Latency (seconds) between the interruption event and its detection.

    Starts at ``USER_INTERRUPT`` (legacy ``interruption``).  Detection is
    the first system reaction to the interrupt: in the project vocabulary
    that is the ``CANCEL`` issued in response, while an explicit legacy
    ``interruption_detected`` event is still recognised when present.
    """
    return _latency(events, start_type, end_type)


def cancellation_latency(events: Sequence[dict],
                         start_type: "str | Sequence[str]" = ("CANCEL", "cancellation"),
                         end_type: "str | Sequence[str]" = ("STATE_UPDATE", "cancelled")) -> Optional[float]:
    """Latency (seconds) between a cancellation request and it taking effect.

    Starts at ``CANCEL`` (legacy ``cancellation``) and ends when the
    cancellation is reflected in state - the ``STATE_UPDATE`` that
    supersedes the cancelled call in the project vocabulary, or an
    explicit legacy ``cancelled`` acknowledgement.
    """
    return _latency(events, start_type, end_type)


def re_planning_latency(events: Sequence[dict],
                        start_type: str = "replan",
                        end_type: str = "replan_done") -> Optional[float]:
    """Latency (seconds) between a re‑planning trigger and its completion.

    The scenario vocabulary (scenarios/*.json) defines no re‑planning
    event yet, so there is no project event name to map; the legacy
    ``replan`` / ``replan_done`` names (or caller‑supplied types) are
    used and the metric returns ``None`` on scenario streams.
    """
    return _latency(events, start_type, end_type)


# ---------------------------------------------------------------------------
# Rate / ratio metrics
# ---------------------------------------------------------------------------

def task_completion_rate(events: Sequence[dict],
                         start_type: str = "task_start",
                         complete_type: str = "task_complete") -> float:
    """Fraction of started tasks that were completed (0.0 – 1.0)."""
    return _rate(events, complete_type, start_type)


def interruption_recovery_rate(events: Sequence[dict],
                               interruption_type: "str | Sequence[str]" = INTERRUPTION_EVENTS,
                               recovery_type: str = "interruption_recovered") -> float:
    """Fraction of interruptions that were successfully recovered (0.0 – 1.0)."""
    return _rate(events, recovery_type, interruption_type)


# ---------------------------------------------------------------------------
# Count metrics
# ---------------------------------------------------------------------------

def stale_tool_calls(events: Sequence[dict],
                     stale_type: "str | Sequence[str]" = STALE_RESULT_EVENTS) -> int:
    """Number of events that represent a stale/late tool result."""
    return _count(events, stale_type)


def duplicate_state_changing_calls(events: Sequence[dict],
                                   change_type: str = "state_change",
                                   id_key: str = "action_id") -> int:
    """Number of duplicate state‑changing calls.

    Counts how many *change_type* events repeat the same *id_key*
    (only the 2nd, 3rd … occurrences are counted as duplicates).
    """
    seen: dict[str, int] = {}
    duplicates = 0
    for ev in events:
        if ev.get("type") != change_type:
            continue
        action_id = ev.get("payload", {}).get(id_key)
        if action_id is None:
            # No id to compare – treat each as unique, no duplicate.
            continue
        key = str(action_id)
        if key in seen:
            duplicates += 1
        else:
            seen[key] = 1
    return duplicates


# ---------------------------------------------------------------------------
# Correctness / validity checks
# ---------------------------------------------------------------------------

def state_snapshot_correctness(events: Sequence[dict],
                               snapshot_type: str = "state_snapshot") -> bool:
    """Check that every state snapshot is well‑formed and ordered.

    Returns True when:
      * at least one snapshot exists,
      * each snapshot has both ``state`` and ``timestamp``,
      * snapshot timestamps are non‑decreasing.
    """
    snapshots = [ev for ev in events if ev.get("type") == snapshot_type]
    if not snapshots:
        return False
    prev_ts: Optional[float] = None
    for snap in snapshots:
        if "state" not in snap or "timestamp" not in snap:
            return False
        try:
            ts = float(snap["timestamp"])
        except (TypeError, ValueError):
            return False
        if prev_ts is not None and ts < prev_ts:
            return False
        prev_ts = ts
    return True


def protocol_validity(events: Sequence[dict],
                      allowed_types: Optional[Sequence[str]] = None) -> bool:
    """Check that every event has required fields and a known type.

    Returns True when:
      * every event contains both ``type`` and ``timestamp``,
      * each ``type`` belongs to *allowed_types* (defaults to a common set).
    """
    if allowed_types is None:
        allowed_types = {
            # Actual event names emitted by the evaluation scenarios.
            "USER_INPUT", "FAST_ACK", "TOOL_CALL", "TOOL_RESULT",
            "FINAL_RESPONSE", "USER_INTERRUPT", "CANCEL", "STATE_UPDATE",
            "STALE_REJECT",
            # Legacy / supporting vocabulary.
            "user_say", "tool_result", "assistant_response",
            "interruption", "interruption_detected", "interruption_recovered",
            "cancellation", "cancelled",
            "replan", "replan_done",
            "task_start", "task_complete",
            "stale_result", "state_change", "state_snapshot",
        }
    for ev in events:
        if "type" not in ev or "timestamp" not in ev:
            return False
        if ev["type"] not in allowed_types:
            return False
    return True


# ---------------------------------------------------------------------------
# Legacy metrics collector (kept for backward compatibility with existing tests)
# ---------------------------------------------------------------------------
class MetricsCollector:
    """Collect simple numeric metrics for an evaluation run.

    Supported metrics (all optional):
      - event_counts: dict mapping event type -> count
      - interruption_count: number of interruption events observed
        (``USER_INTERRUPT``; legacy ``interruption`` also accepted)
      - stale_result_count: number of late/superseded result events
        (``STALE_REJECT``; legacy ``stale_result`` also accepted)
      - durations: list of float durations (seconds) observed
    """

    def __init__(self) -> None:
        self._event_counts: dict[str, int] = {}
        self._interruption_count = 0
        self._stale_result_count = 0
        self._durations: list[float] = []

    def add_event(self, event: dict) -> None:
        """Register an event for metric aggregation."""
        event_type = event.get("type", "unknown")
        self._event_counts[event_type] = self._event_counts.get(event_type, 0) + 1
        if event_type in INTERRUPTION_EVENTS:
            self._interruption_count += 1
        if event_type in STALE_RESULT_EVENTS:
            self._stale_result_count += 1
        dur = event.get("duration")
        if dur is not None:
            try:
                self._durations.append(float(dur))
            except (TypeError, ValueError):
                pass

    def get_counts(self) -> dict:
        """Return a snapshot of the collected metrics."""
        return {
            "event_counts": dict(self._event_counts),
            "interruption_count": self._interruption_count,
            "stale_result_count": self._stale_result_count,
            "durations": list(self._durations),
        }

    def clear(self) -> None:
        """Reset the collector for a new run."""
        self._event_counts.clear()
        self._interruption_count = 0
        self._stale_result_count = 0
        self._durations.clear()