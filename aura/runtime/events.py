"""Immutable event model for the AURA real-time runtime (Step 2).

An :class:`Event` is a *fact that happened* in one session. Events are the only
thing that flows into the future Coordinator::

    Event  ->  SessionMailbox  ->  Coordinator (future)

Design notes
------------
- Frozen dataclass: an event is never mutated after creation.
- ``seq`` on an event constructed by a producer is a *placeholder*; the
  authoritative sequence number is assigned by the mailbox when the event is
  accepted (see ``aura.runtime.mailbox``). Producers are never trusted for
  ordering.
- ``payload`` is a flexible mapping (mock-friendly). Mapping payloads are
  shallow-copied and wrapped read-only, so an event cannot be mutated through
  a reference the caller keeps.
- No global ordering, no persistence, no external dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

__all__ = ["EventType", "Event", "new_event_id"]


class EventType(str, Enum):
    """The deliberately small set of event types for the first runtime steps."""

    USER_INPUT = "user.input"
    USER_INTERRUPT = "user.interrupt"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCEL_REQUESTED = "task.cancel_requested"
    TASK_CANCELLED = "task.cancelled"
    TASK_RESULT_REJECTED = "task.result_rejected"
    STATE_VERSION_CHANGED = "state.version_changed"
    PLAN_READY = "plan.ready"
    PLAN_FAILED = "plan.failed"
    RUNTIME_ERROR = "runtime.error"

    def __str__(self) -> str:
        return self.value


def new_event_id() -> str:
    """Return a globally unique event id (standard library, no dependencies)."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable, session-scoped fact.

    Intended to be built with keyword arguments; only ``session_id`` and
    ``event_type`` are required.
    """

    session_id: str
    event_type: EventType
    event_id: str = field(default_factory=new_event_id)
    seq: int = 0  # placeholder until the mailbox assigns the authoritative value
    timestamp: float = field(default_factory=time.time)  # wall-clock seconds
    actor: str = "system"
    payload: Any = field(default_factory=dict)
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(self.event_type, EventType):
            object.__setattr__(self, "event_type", EventType(self.event_type))
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 0:
            raise ValueError("seq must be a non-negative integer")
        object.__setattr__(self, "timestamp", float(self.timestamp))
        if not isinstance(self.actor, str) or not self.actor:
            raise ValueError("actor must be a non-empty string")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 0
        ):
            raise ValueError("version must be a non-negative integer")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str) or not self.correlation_id
        ):
            raise ValueError("correlation_id must be None or a non-empty string")
        if self.causation_id is not None and (
            not isinstance(self.causation_id, str) or not self.causation_id
        ):
            raise ValueError("causation_id must be None or a non-empty string")

        payload = self.payload
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping (or None)")
        # Shallow-copy + wrap read-only: callers cannot mutate the event later.
        object.__setattr__(self, "payload", MappingProxyType(dict(payload)))
