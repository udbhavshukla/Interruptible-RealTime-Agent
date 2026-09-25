"""State schemas: typed session state, snapshots, call bindings, errors.

Design: stdlib dataclasses only (no extra framework) so Member 1/2/4 can
import without dependency weight. Snapshots are frozen (immutable).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Slot schemas per intent (fixed, validated; unknown slots rejected)
# ---------------------------------------------------------------------------

SLOT_SCHEMAS: Dict[str, frozenset] = {
    "flight_search": frozenset({"origin", "destination", "date"}),
    "support_ticket": frozenset({"device_model", "error_code", "description"}),
    "booking": frozenset({"option_id", "passenger", "idempotency_key"}),
}

# Intents with no fixed schema allow no slots (safer default). Register new
# intents here rather than silently accepting arbitrary slots.
KNOWN_INTENTS = frozenset(SLOT_SCHEMAS.keys()) | frozenset({"none", "chitchat"})


# ---------------------------------------------------------------------------
# Errors (structured, never swallowed silently by the manager)
# ---------------------------------------------------------------------------

class StateError(Exception):
    """Base class for state errors."""


class UnknownSessionError(StateError):
    pass


class InvalidSlotError(StateError):
    def __init__(self, slot: str, intent: Optional[str]):
        super().__init__(f"Unknown slot '{slot}' for intent '{intent}'")
        self.slot = slot
        self.intent = intent


class InvalidIntentError(StateError):
    pass


class DuplicateCallError(StateError):
    pass


class UnknownCallError(StateError):
    pass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallBinding:
    """A tool call bound to the state version it was planned against.

    Binding only -- deliberately NO execution-lifecycle status. Lifecycle
    (pending/running/completed/failed/cancelled/committed) is owned solely
    by ToolRegistry; see docs/member3.md "Call Ownership".
    """
    call_id: str
    tool_name: str
    state_version: int
    args: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: Optional[str] = None


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable snapshot of one session at one version.

    Frozen dataclass + defensive copies: callers cannot mutate history.
    """
    session_id: str
    intent: str
    slots: Dict[str, Any]
    state_version: int
    active_calls: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "intent": self.intent,
            "slots": copy.deepcopy(self.slots),
            "state_version": self.state_version,
            "active_calls": list(self.active_calls),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateSnapshot":
        return cls(
            session_id=d["session_id"],
            intent=d.get("intent", "none"),
            slots=copy.deepcopy(d.get("slots", {})),
            state_version=int(d.get("state_version", 0)),
            active_calls=list(d.get("active_calls", [])),
        )


def validate_slots(intent: str, updates: Dict[str, Any]) -> None:
    """Raise InvalidSlotError if any slot is unknown for the intent.

    Intents 'none'/'chitchat' accept no slots. Unknown intents are rejected
    by the manager (InvalidIntentError) before this is reached.
    """
    if intent in ("none", "chitchat"):
        if updates:
            raise InvalidSlotError(next(iter(updates)), intent)
        return
    allowed = SLOT_SCHEMAS.get(intent)
    if allowed is None:
        raise InvalidIntentError(f"Unknown intent '{intent}'")
    for slot in updates:
        if slot not in allowed:
            raise InvalidSlotError(slot, intent)
