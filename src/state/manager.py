"""Session-scoped State Manager.

Guarantees:
- session isolation (no cross-session leakage, no global user state)
- localized slot corrections (only touched slots change)
- monotonically increasing state_version
- immutable snapshots + full history for replay/audit
- tool calls bound to the state_version they were planned against
- deterministic stale-result rejection via is_stale()
- idempotent-friendly explicit commit semantics live in tools layer;
  here the manager only tracks call bindings + versions.

Thread-safe via one lock. All state ops are lightweight (no I/O).
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Dict, List, Optional

from .schemas import (
    SLOT_SCHEMAS,
    CallBinding,
    DuplicateCallError,
    InvalidIntentError,
    StateSnapshot,
    UnknownCallError,
    UnknownSessionError,
    validate_slots,
)


class _SessionRecord:
    __slots__ = ("intent", "slots", "state_version", "calls", "history")

    def __init__(self) -> None:
        self.intent: str = "none"
        self.slots: Dict[str, Any] = {}
        self.state_version: int = 0
        self.calls: Dict[str, CallBinding] = {}
        self.history: List[StateSnapshot] = []

    def snapshot(self, session_id: str) -> StateSnapshot:
        return StateSnapshot(
            session_id=session_id,
            intent=self.intent,
            slots=copy.deepcopy(self.slots),
            state_version=self.state_version,
            active_calls=sorted(self.calls.keys()),
        )


class StateManager:
    """Session-scoped state store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, _SessionRecord] = {}

    # -- sessions ------------------------------------------------------
    def create_session(self, session_id: str, intent: str = "none") -> StateSnapshot:
        """Create (or reset to fresh) a session. Fresh sessions start at v0."""
        if not session_id:
            raise ValueError("session_id must be non-empty")
        with self._lock:
            rec = _SessionRecord()
            if intent != "none":
                if intent not in SLOT_SCHEMAS and intent != "chitchat":
                    raise InvalidIntentError(f"Unknown intent '{intent}'")
                rec.intent = intent
            self._sessions[session_id] = rec
            snap = rec.snapshot(session_id)
            rec.history.append(snap)
            # Return a copy: the history entry must never alias a
            # caller-visible handle.
            return copy.deepcopy(snap)

    def _require(self, session_id: str) -> _SessionRecord:
        rec = self._sessions.get(session_id)
        if rec is None:
            raise UnknownSessionError(f"Unknown session '{session_id}'")
        return rec

    def get_state(self, session_id: str) -> StateSnapshot:
        with self._lock:
            return self._require(session_id).snapshot(session_id)

    def get_snapshot(self, session_id: str) -> StateSnapshot:
        return self.get_state(session_id)

    def get_history(self, session_id: str) -> List[StateSnapshot]:
        with self._lock:
            rec = self._require(session_id)
            # Deep copies: frozen snapshots still contain mutable dicts, and
            # history entries must never alias caller-visible handles.
            return copy.deepcopy(rec.history)

    def current_version(self, session_id: str) -> int:
        with self._lock:
            return self._require(session_id).state_version

    # -- mutations (each bumps version iff something changed) -----------
    def _commit(self, session_id: str, rec: _SessionRecord) -> StateSnapshot:
        rec.state_version += 1
        snap = rec.snapshot(session_id)
        rec.history.append(snap)
        # Return a copy: the history entry must never alias a
        # caller-visible handle.
        return copy.deepcopy(snap)

    def update_intent(self, session_id: str, intent: str) -> StateSnapshot:
        with self._lock:
            rec = self._require(session_id)
            if intent not in SLOT_SCHEMAS and intent not in ("none", "chitchat"):
                raise InvalidIntentError(f"Unknown intent '{intent}'")
            if rec.intent == intent:
                return rec.snapshot(session_id)  # no-op: no version bump
            rec.intent = intent
            # Switching intent clears slots (new task frame), documented.
            rec.slots = {}
            return self._commit(session_id, rec)

    def update_slot(self, session_id: str, slot_name: str, value: Any) -> StateSnapshot:
        return self.update_slots(session_id, {slot_name: value})

    def update_slots(self, session_id: str, updates: Dict[str, Any]) -> StateSnapshot:
        """Localized correction: only listed slots change; others preserved."""
        with self._lock:
            rec = self._require(session_id)
            validate_slots(rec.intent, dict(updates))
            if not updates:
                return rec.snapshot(session_id)
            changed = False
            for k, v in updates.items():
                if rec.slots.get(k) != v:
                    rec.slots[k] = copy.deepcopy(v)
                    changed = True
            if not changed:
                return rec.snapshot(session_id)  # idempotent no-op
            return self._commit(session_id, rec)

    def validate_state(self, session_id: str) -> Dict[str, Any]:
        """Return {'ok': bool, 'missing': [...]} vs the intent's slot schema."""
        with self._lock:
            rec = self._require(session_id)
            if rec.intent in ("none", "chitchat"):
                return {"ok": True, "missing": []}
            allowed = SLOT_SCHEMAS.get(rec.intent, frozenset())
            missing = [s for s in sorted(allowed) if s not in rec.slots]
            # booking intent: idempotency_key optional until commit time
            if rec.intent == "booking" and "idempotency_key" in missing:
                missing.remove("idempotency_key")
            return {"ok": not missing, "missing": missing}

    # -- call bindings --------------------------------------------------
    def register_call(
        self,
        session_id: str,
        call_id: str,
        state_version: int,
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> CallBinding:
        with self._lock:
            rec = self._require(session_id)
            if call_id in rec.calls:
                raise DuplicateCallError(f"Duplicate call_id '{call_id}'")
            binding = CallBinding(
                call_id=call_id,
                tool_name=tool_name,
                state_version=int(state_version),
                args=copy.deepcopy(args or {}),
                idempotency_key=idempotency_key,
            )
            rec.calls[call_id] = binding
            # Return a copy: CallBinding.args is a mutable dict and the
            # stored binding must never alias a caller-visible handle.
            return copy.deepcopy(binding)

    def invalidate_call(self, session_id: str, call_id: str) -> bool:
        """Idempotent invalidation for cancel/supersede/obsolete/interrupt.

        Meaning: "this call binding is no longer allowed to produce an
        accepted result for this session." After this, is_stale() rejects
        late results for the call. Returns True if a live binding was
        dropped, False if none existed (safe to call twice).

        This is semantically different from registry cancellation (which
        owns execution/lifecycle): the Coordinator must perform BOTH --
        registry.cancel_call() then state.invalidate_call(). Do NOT call
        this on successful completion; completed bindings stay intact so
        their results are accepted.
        """
        with self._lock:
            rec = self._require(session_id)
            if call_id not in rec.calls:
                return False
            del rec.calls[call_id]
            return True

    def remove_call(self, session_id: str, call_id: str) -> None:
        """Strict removal. Compatibility wrapper over invalidate_call():
        unknown calls raise UnknownCallError. New cancel/supersede flows
        should prefer invalidate_call(), which is idempotent."""
        if not self.invalidate_call(session_id, call_id):
            raise UnknownCallError(f"Unknown call '{call_id}'")

    def get_call(self, session_id: str, call_id: str) -> CallBinding:
        with self._lock:
            rec = self._require(session_id)
            binding = rec.calls.get(call_id)
            if binding is None:
                raise UnknownCallError(f"Unknown call '{call_id}'")
            return copy.deepcopy(binding)

    def is_stale(
        self,
        session_id: str,
        state_version: int,
        call_id: Optional[str] = None,
    ) -> bool:
        """Deterministic stale-result check.

        Stale if:
        - result version < current version (superseded by newer intent), or
        - call_id given but no longer active (cancelled/finished/unknown).
        A result from the future (version > current) is NOT stale here;
        callers should treat it as a contract violation separately.
        """
        with self._lock:
            rec = self._require(session_id)
            if int(state_version) < rec.state_version:
                return True
            if call_id is not None and call_id not in rec.calls:
                return True
            return False
