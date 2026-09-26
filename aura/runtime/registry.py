"""Task registry for the AURA real-time runtime (Step 3).

The registry is the runtime's *bookkeeper* for async tool calls. It records
what each call is, which session version spawned it, where it sits in its
lifecycle, and whether cancellation was requested::

    register -> mark_running -> request_cancel -> ... -> terminal state
                                (SUCCEEDED / FAILED / CANCELLED)

Design notes
------------
- **Records are retained after completion or cancellation.** A late result
  (the T9/T10 scenario) must still be inspectable long after the task
  settled, so terminal records are never deleted.
- **Two independent facts are tracked**: ``state`` records what actually
  happened; ``cancel_requested`` records what was *asked*. A tool that
  ignores cancellation and succeeds anyway ends as ``SUCCEEDED`` with
  ``cancel_requested=True`` — the future Acceptance Gate rejects that result
  using the flag, not the state.
- **Cancellation requests are idempotent**: repeating ``request_cancel`` (or
  calling it on an already-settled record) is a no-op, never an error.
- Timestamps are wall-clock seconds (``time.time``) and every mutating
  method accepts an optional ``at`` so tests and future deterministic runs
  can inject times instead of sleeping.
- Standard library only. No asyncio execution, no Coordinator, no
  Acceptance Gate here — those are separate steps.

Lifecycle::

    CREATED ──mark_running──► RUNNING ──mark_succeeded──► SUCCEEDED
       │                       │  │  └──mark_failed─────► FAILED
       │                       │  └──request_cancel──► CANCEL_REQUESTED
       │                       │                           │
       ├──request_cancel───────┴───────────────────────────┤
       ├──mark_cancelled─────────────────────────────► CANCELLED
       └──mark_failed────────────────────────────────► FAILED
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

__all__ = [
    "TaskState",
    "TaskRecord",
    "TaskRegistry",
    "DuplicateCallError",
    "UnknownCallError",
    "InvalidTransition",
]


class TaskState(str, Enum):
    """Lifecycle state of one tool/task call."""

    CREATED = "created"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES: FrozenSet[TaskState] = frozenset(
    {TaskState.CANCELLED, TaskState.SUCCEEDED, TaskState.FAILED}
)

# Which states each transition may start from.
_RUNNING_FROM: FrozenSet[TaskState] = frozenset({TaskState.CREATED})
_CANCEL_REQUESTED_FROM: FrozenSet[TaskState] = frozenset(
    {TaskState.CREATED, TaskState.RUNNING}
)
_CANCELLED_FROM: FrozenSet[TaskState] = frozenset(
    {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCEL_REQUESTED}
)
_SUCCEEDED_FROM: FrozenSet[TaskState] = frozenset(
    {TaskState.RUNNING, TaskState.CANCEL_REQUESTED}
)
_FAILED_FROM: FrozenSet[TaskState] = frozenset(
    {TaskState.CREATED, TaskState.RUNNING, TaskState.CANCEL_REQUESTED}
)


class DuplicateCallError(ValueError):
    """Raised when registering a call_id that already exists."""


class UnknownCallError(KeyError):
    """Raised when an operation references a call_id the registry does not know."""


class InvalidTransition(RuntimeError):
    """Raised when a lifecycle transition is not allowed from the current state."""


@dataclass
class TaskRecord:
    """Mutable bookkeeping for a single async tool/task call.

    Owned by the registry: mutate only through :class:`TaskRegistry` methods
    so the lifecycle rules above stay enforced.
    """

    call_id: str
    tool_name: str
    spawn_version: int
    plan_id: Optional[str] = None
    step_id: Optional[str] = None
    state: TaskState = TaskState.CREATED
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class TaskRegistry:
    """Per-session bookkeeping for task lifecycle and cancellation flags.

    Small API for the future Coordinator::

        registry.register(call_id=..., tool_name=..., spawn_version=...)
        registry.mark_running(call_id)
        registry.request_cancel(call_id)
        registry.mark_succeeded(call_id)   # or mark_failed / mark_cancelled
        registry.get(call_id)
    """

    def __init__(self) -> None:
        self._records: Dict[str, TaskRecord] = {}

    # ----------------------------------------------------------- registration
    def register(
        self,
        *,
        call_id: str,
        tool_name: str,
        spawn_version: int,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        at: Optional[float] = None,
    ) -> TaskRecord:
        """Create a ``CREATED`` record for a new call. Duplicate ids are rejected."""
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("call_id must be a non-empty string")
        if call_id in self._records:
            raise DuplicateCallError(f"call_id {call_id!r} is already registered")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("tool_name must be a non-empty string")
        if (
            isinstance(spawn_version, bool)
            or not isinstance(spawn_version, int)
            or spawn_version < 0
        ):
            raise ValueError("spawn_version must be a non-negative integer")
        for name, value in (("plan_id", plan_id), ("step_id", step_id)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be None or a non-empty string")

        record = TaskRecord(
            call_id=call_id,
            tool_name=tool_name,
            spawn_version=spawn_version,
            plan_id=plan_id,
            step_id=step_id,
            created_at=self._timestamp(at),
        )
        self._records[call_id] = record
        return record

    # ------------------------------------------------------------------ lookup
    def get(self, call_id: str) -> TaskRecord:
        record = self._records.get(call_id)
        if record is None:
            raise UnknownCallError(f"unknown call_id {call_id!r}")
        return record

    def __contains__(self, call_id: object) -> bool:
        return call_id in self._records

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> Tuple[TaskRecord, ...]:
        """All records in registration order (retained forever, incl. terminal)."""
        return tuple(self._records.values())

    # ------------------------------------------------------- lifecycle methods
    def mark_running(self, call_id: str, *, at: Optional[float] = None) -> TaskRecord:
        """``CREATED -> RUNNING``; stamps ``started_at``.

        Re-running an already ``RUNNING`` task is a no-op (idempotent); any
        other state is rejected.
        """
        record = self.get(call_id)
        if record.state is TaskState.RUNNING:
            return record
        self._require(record, _RUNNING_FROM, "mark_running")
        record.state = TaskState.RUNNING
        record.started_at = self._timestamp(at)
        return record

    def request_cancel(
        self, call_id: str, *, at: Optional[float] = None
    ) -> TaskRecord:
        """``CREATED|RUNNING -> CANCEL_REQUESTED`` and set ``cancel_requested``.

        Idempotent: repeat requests, and requests against a record that is
        already ``CANCEL_REQUESTED`` or already terminal, are no-ops. ``at``
        is accepted for API symmetry but cancellation requests do not stamp
        ``finished_at`` (the task has not settled yet).
        """
        record = self.get(call_id)
        if record.state is TaskState.CANCEL_REQUESTED or record.state.is_terminal:
            return record  # idempotent no-op
        self._require(record, _CANCEL_REQUESTED_FROM, "request_cancel")
        record.state = TaskState.CANCEL_REQUESTED
        record.cancel_requested = True
        return record

    def mark_cancelled(
        self, call_id: str, *, at: Optional[float] = None
    ) -> TaskRecord:
        """``CANCEL_REQUESTED|RUNNING|CREATED -> CANCELLED``; stamps ``finished_at``."""
        record = self.get(call_id)
        self._require(record, _CANCELLED_FROM, "mark_cancelled")
        record.state = TaskState.CANCELLED
        record.cancel_requested = True
        record.finished_at = self._timestamp(at)
        return record

    def mark_succeeded(
        self, call_id: str, *, at: Optional[float] = None
    ) -> TaskRecord:
        """``RUNNING|CANCEL_REQUESTED -> SUCCEEDED``; stamps ``finished_at``.

        Allowed from ``CANCEL_REQUESTED`` on purpose: a tool that ignores
        cancellation really did succeed. ``cancel_requested`` stays ``True``
        so the future Acceptance Gate can still reject its result.
        """
        record = self.get(call_id)
        self._require(record, _SUCCEEDED_FROM, "mark_succeeded")
        record.state = TaskState.SUCCEEDED
        record.finished_at = self._timestamp(at)
        return record

    def mark_failed(self, call_id: str, *, at: Optional[float] = None) -> TaskRecord:
        """``CREATED|RUNNING|CANCEL_REQUESTED -> FAILED``; stamps ``finished_at``."""
        record = self.get(call_id)
        self._require(record, _FAILED_FROM, "mark_failed")
        record.state = TaskState.FAILED
        record.finished_at = self._timestamp(at)
        return record

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _timestamp(at: Optional[float]) -> float:
        return time.time() if at is None else float(at)

    @staticmethod
    def _require(
        record: TaskRecord, allowed: FrozenSet[TaskState], operation: str
    ) -> None:
        if record.state not in allowed:
            raise InvalidTransition(
                f"cannot {operation}({record.call_id!r}) from state "
                f"{record.state.value!r} (allowed from: "
                f"{sorted(s.value for s in allowed)})"
            )
