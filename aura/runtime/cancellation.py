"""Runtime-level cancellation for the AURA real-time runtime (Step 5).

The Coordinator never touches an ``asyncio.Task``; it asks this manager::

    Coordinator
        |
        v
    CancellationManager.request(call_id) -> CancelOutcome      (this module)
        |        - validates the call exists
        |        - records intent in the TaskRegistry FIRST (tombstone)
        v
    TaskSupervisor.cancel(call_id)                             (Step 4)
        |
        v
    asyncio.Task.cancel()                                      (best effort)

Design notes
------------
- **Cancellation is advisory.** A successful outcome means the *request* was
  recorded and delegated — never that the task has terminated. A tool may
  ignore or delay cancellation and still finish; in that case the registry
  keeps ``cancel_requested=True`` and the task settles on its own. The future
  Acceptance Gate (a later step) uses that flag to reject stale results.
- **Tombstone first, signal second**: intent is written to the registry
  before the supervisor is asked to signal, so a result arriving in the same
  tick can never beat the record.
- **Outcomes are classified** so the Coordinator can branch without
  exceptions: ``REQUESTED`` / ``ALREADY_REQUESTED`` / ``ALREADY_CANCELLED`` /
  ``ALREADY_COMPLETED``. An *unknown* ``call_id`` raises
  :class:`~aura.runtime.registry.UnknownCallError` (the codebase-wide
  convention for lookups) rather than returning a sentinel.
- **Records are never deleted** — late/stale results must stay inspectable.
- This module never imports or manipulates ``asyncio`` objects directly.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from .registry import TaskRegistry, TaskState

if TYPE_CHECKING:  # no runtime import: avoids a supervisor <-> manager cycle
    from .supervisor import TaskSupervisor

__all__ = ["CancelOutcome", "CancellationManager"]


class CancelOutcome(str, Enum):
    """Classification of a cancellation *request* (never of task termination)."""

    #: Intent was newly recorded in the registry and delegated to the supervisor.
    REQUESTED = "requested"
    #: Intent was already on record; the (safe) request was re-delegated.
    ALREADY_REQUESTED = "already_requested"
    #: The call already settled as CANCELLED — nothing left to cancel.
    ALREADY_CANCELLED = "already_cancelled"
    #: The call already settled as SUCCEEDED or FAILED — too late to cancel.
    ALREADY_COMPLETED = "already_completed"

    def __str__(self) -> str:
        return self.value


class CancellationManager:
    """The Coordinator's cancellation API (thin, advisory, registry-first)."""

    def __init__(self, supervisor: "TaskSupervisor") -> None:
        self._supervisor = supervisor

    @property
    def supervisor(self) -> "TaskSupervisor":
        return self._supervisor

    @property
    def registry(self) -> TaskRegistry:
        """Single source of truth — always the supervisor's registry."""
        return self._supervisor.registry

    def request(self, call_id: str) -> CancelOutcome:
        """Request cancellation of ``call_id``.

        Sequence: validate -> classify -> record intent in the registry ->
        delegate the asyncio signal to the supervisor.

        Returns a :class:`CancelOutcome` for every *known* call.
        Raises :class:`~aura.runtime.registry.UnknownCallError` when the call
        does not exist — unknown calls are never silently ignored.

        ``REQUESTED``/``ALREADY_REQUESTED`` only certify that the request was
        recorded and delegated; they do **not** mean the task terminated or
        will terminate.
        """
        record = self.registry.get(call_id)  # validate: unknown -> raises

        if record.state.is_terminal:
            if record.state is TaskState.CANCELLED:
                return CancelOutcome.ALREADY_CANCELLED
            return CancelOutcome.ALREADY_COMPLETED  # SUCCEEDED / FAILED

        already_requested = (
            record.state is TaskState.CANCEL_REQUESTED or record.cancel_requested
        )

        # 1) Intent through the registry first (idempotent; tombstone).
        self.registry.request_cancel(call_id)
        # 2) Delegate the actual signal. Best-effort; may do nothing if the
        #    task already finished or has no asyncio backing (still safe).
        self._supervisor.cancel(call_id)

        if already_requested:
            return CancelOutcome.ALREADY_REQUESTED
        return CancelOutcome.REQUESTED
