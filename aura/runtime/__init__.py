"""AURA real-time / concurrency runtime (Member 2).

Responsibilities of this package:

- event processing and per-session event ordering
- mailbox / queues (ingress lanes, coalescing, backpressure, dead-letter)
- task registry and task lifecycle
- task supervision (spawn, watchdog, exactly-one outcome event)
- cancellation (tombstone-then-signal)
- stale-result protection (acceptance gate)
- the Coordinator (single writer per session)

Public imports
--------------
The common names are re-exported here, so callers can simply write::

    from aura.runtime import Coordinator, SessionMailbox, Event

Importing straight from a submodule (``from aura.runtime.coordinator
import Coordinator``) works equally well. The demo is deliberately *not*
re-exported: importing ``.demo`` at package level would make
``python -m aura.runtime.demo`` emit a runpy warning. Import or run it via
its own module instead.

Delivered so far:

- Step 1: importable package shell.
- Step 2: ``aura.runtime.events`` (immutable ``Event`` / ``EventType``) and
  ``aura.runtime.mailbox`` (``SessionMailbox``: bounded, CONTROL/DATA
  priority, per-session isolation, mailbox-assigned sequence numbers).
- Step 3: ``aura.runtime.registry`` (``TaskRegistry`` / ``TaskRecord`` /
  ``TaskState``: lifecycle bookkeeping, idempotent cancellation requests,
  retained records for late results).
- Step 4: ``aura.runtime.supervisor`` (``TaskSupervisor``: spawns supervised
  ``asyncio.Task``\\ s, settles the registry on every outcome, advisory
  idempotent cancellation).
- Step 5: ``aura.runtime.cancellation`` (``CancellationManager`` /
  ``CancelOutcome``: registry-first cancellation intent, delegated to the
  supervisor, classified outcomes).

- Step 6: ``aura.runtime.acceptance`` (``AcceptanceGate`` / ``Decision`` /
  ``GateDecision``: authoritative stale-result protection — cancellation is
  advisory, acceptance is authoritative).
- Step 7: ``aura.runtime.state`` (``SessionState``: immutable versioned
  snapshots, deterministic v1, controlled ``derive()`` increments).
- Step 8: ``aura.runtime.coordinator`` (``Coordinator``: the single writer
  that consumes ``SessionMailbox`` events in sequence order, advances the
  session version on input/interrupt, requests advisory cancellation of
  obsolete calls, spawns version-tagged work through the supervisor, and
  filters every outcome through the Acceptance Gate).
- Step 9: ``aura.runtime.demo`` (deterministic end-to-end T0-T12 scenario:
  interrupt -> v2 -> stale rejection -> accepted Mumbai result, with a
  printable presentation trace; run via ``python -m aura.runtime.demo``).

Planned modules (NOT yet created):

- none — the runtime core (Steps 1-9) is complete.
"""

from .acceptance import AcceptanceGate, Decision, GateDecision
from .cancellation import CancelOutcome, CancellationManager
from .coordinator import Coordinator, ToolCallable
from .events import Event, EventType, new_event_id
from .mailbox import MailboxFull, Priority, SessionMailbox, SessionMismatchError
from .registry import (
    DuplicateCallError,
    InvalidTransition,
    TaskRecord,
    TaskRegistry,
    TaskState,
    UnknownCallError,
)
from .state import INITIAL_VERSION, SessionState
from .supervisor import CoroFactory, TaskSupervisor

__all__ = [
    # events & mailbox (ingress, ordering)
    "Event",
    "EventType",
    "new_event_id",
    "Priority",
    "SessionMailbox",
    "MailboxFull",
    "SessionMismatchError",
    # registry & supervisor (task lifecycle)
    "TaskState",
    "TaskRecord",
    "TaskRegistry",
    "DuplicateCallError",
    "UnknownCallError",
    "InvalidTransition",
    "TaskSupervisor",
    "CoroFactory",
    # cancellation & acceptance (authoritative verdicts)
    "CancellationManager",
    "CancelOutcome",
    "AcceptanceGate",
    "Decision",
    "GateDecision",
    # session state & the single writer
    "SessionState",
    "INITIAL_VERSION",
    "Coordinator",
    "ToolCallable",
]
