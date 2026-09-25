"""Acceptance gate: authoritative stale-result protection (Step 6).

Core rule — **cancellation is advisory; acceptance/rejection is
authoritative.** A result may be accepted only when its execution context
still matches the current session context::

    Coordinator (future)
          |
          v
    AcceptanceGate.evaluate(call_id, current_version) -> GateDecision
          |
          +-- read-only lookup in TaskRegistry (session state is never mutated)

Decision precedence (checked in this exact order):

 1. ``INVALID``       malformed inputs (bad ``call_id`` / ``current_version``)
 2. ``UNKNOWN_CALL``  well-formed id that has no task record
 3. ``STALE``         ``spawn_version != current_version``  <-- the rule
 4. ``CANCELLED``     cancellation was requested (or task is CANCELLED)
 5. ``INVALID``       record holds no result (state is not SUCCEEDED)
 6. ``ACCEPTED``      same version, never cancel-requested, SUCCEEDED

Why version is checked *before* cancellation — the canonical T9/T10 case::

    call_001  spawn_version = 1
    session  current_version = 2
    call_001 returns successfully (it ignored/outran cancellation)

    evaluate("call_001", 2)  ->  STALE        (never ACCEPTED)

The asyncio cancellation arriving too late, the successful completion, the
ignored cancel, or a newer plan existing are all irrelevant: the version
mismatch alone decides, while ``cancel_requested`` stays visible in the
decision for explanation.

Design notes
------------
- ``evaluate`` never raises for any input — every case maps to one of the
  five decisions (the Coordinator can branch without try/except).
- The gate is **stateless**: the current session version is passed in per
  call (the future Coordinator owns session state; this step must not build
  a state store).
- :class:`GateDecision` is a frozen record carrying enough metadata to
  explain and audit any rejection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .registry import TaskRegistry, TaskState, UnknownCallError

__all__ = ["Decision", "GateDecision", "AcceptanceGate"]


class Decision(str, Enum):
    """Authoritative verdict for one candidate result."""

    ACCEPTED = "accepted"
    STALE = "stale"
    UNKNOWN_CALL = "unknown_call"
    CANCELLED = "cancelled"
    INVALID = "invalid"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Immutable, traceable verdict: what was decided and why."""

    decision: Decision
    call_id: str
    reason: str
    spawn_version: Optional[int] = None  # None when there is no record
    current_version: Optional[int] = None
    state: Optional[TaskState] = None
    cancel_requested: bool = False


class AcceptanceGate:
    """Read-only stale-result filter the Coordinator consults on every result.

    ``AcceptanceGate(registry)`` — needs the TaskRegistry for metadata and
    nothing else; session version is an argument, never stored.
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    def evaluate(self, call_id: str, current_version: int) -> GateDecision:
        """Classify whether ``call_id``'s result may be accepted *now*.

        Pure and side-effect free: it reads the registry and returns a
        :class:`GateDecision`; it never mutates the record, the registry, or
        any session state.
        """
        # 1) malformed inputs ------------------------------------------------
        if not isinstance(call_id, str) or not call_id:
            return GateDecision(
                decision=Decision.INVALID,
                call_id=call_id if isinstance(call_id, str) else repr(call_id),
                reason=f"call_id must be a non-empty string, got {call_id!r}",
            )
        if (
            isinstance(current_version, bool)
            or not isinstance(current_version, int)
            or current_version < 0
        ):
            return GateDecision(
                decision=Decision.INVALID,
                call_id=call_id,
                reason=(
                    "current_version must be a non-negative integer, "
                    f"got {current_version!r}"
                ),
            )

        # 2) does the call exist? -------------------------------------------
        try:
            record = self._registry.get(call_id)
        except UnknownCallError:
            return GateDecision(
                decision=Decision.UNKNOWN_CALL,
                call_id=call_id,
                current_version=current_version,
                reason=f"no task record exists for call_id {call_id!r}",
            )

        def verdict(decision: Decision, reason: str) -> GateDecision:
            return GateDecision(
                decision=decision,
                call_id=call_id,
                reason=reason,
                spawn_version=record.spawn_version,
                current_version=current_version,
                state=record.state,
                cancel_requested=record.cancel_requested,
            )

        # 3) the authoritative rule: stale beats everything ------------------
        if record.spawn_version != current_version:
            return verdict(
                Decision.STALE,
                f"spawn_version {record.spawn_version} does not match current "
                f"session version {current_version}",
            )

        # 4) cancellation (advisory signal, authoritative rejection) ----------
        if record.cancel_requested or record.state is TaskState.CANCELLED:
            return verdict(
                Decision.CANCELLED,
                "cancellation was requested for this call "
                f"(state={record.state.value})",
            )

        # 5) is there even a result to accept? --------------------------------
        if record.state is not TaskState.SUCCEEDED:
            return verdict(
                Decision.INVALID,
                f"no result to accept: call {call_id!r} is in state "
                f"{record.state.value}",
            )

        # 6) accepted ---------------------------------------------------------
        return verdict(
            Decision.ACCEPTED,
            "spawn_version matches current session version, no cancellation "
            "was requested, and the call succeeded",
        )
