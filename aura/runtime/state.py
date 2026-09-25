"""Versioned session state for the AURA real-time runtime (Step 7).

AURA uses optimistic versioning::

    v1  --user correction ("Actually Mumbai, not Delhi.")-->  v2

Every meaningful mutation produces a **new** snapshot; existing snapshots are
never mutated. That is what makes snapshots safe to hand to async work: a
task spawned under ``v1`` keeps reading ``v1`` forever, even after the
session has advanced to ``v2`` — which is exactly what the Acceptance Gate
needs to declare a late ``v1`` result ``STALE``.

Design notes
------------
- :class:`SessionState` is a frozen dataclass; ``metadata`` is shallow-copied
  and wrapped read-only (same rule as ``Event.payload``).
- **Deterministic start**: ``SessionState.initial(...)`` always yields
  ``version == INITIAL_VERSION`` (1).
- **Controlled increments**: the only way to get version ``N + 1`` is
  ``snapshot.derive(...)`` — no ``version`` argument exists, so callers cannot
  skip, repeat, or rewind versions. Fields you don't override carry over;
  passing ``None`` for ``plan_id``/``metadata`` explicitly clears them.
- **Single-writer rule** (the future Coordinator's job): derive from *current*
  only. Deriving from an old snapshot re-creates that version number, so the
  Coordinator must always advance from the latest snapshot — the same
  single-writer discipline the Coordinator already enforces elsewhere.
- No Coordinator, no persistence, no database: this module only models the
  snapshot and how the next version is minted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

__all__ = ["SessionState", "INITIAL_VERSION"]

#: Deterministic first version of every session.
INITIAL_VERSION = 1

_UNSET = object()  # distinguish "keep current value" from an explicit None


@dataclass(frozen=True, slots=True)
class SessionState:
    """Immutable snapshot of one session at one version."""

    session_id: str
    version: int
    intent: Any = None
    plan_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < INITIAL_VERSION
        ):
            raise ValueError(
                f"version must be an integer >= {INITIAL_VERSION} "
                f"(got {self.version!r})"
            )
        if self.plan_id is not None and (
            not isinstance(self.plan_id, str) or not self.plan_id
        ):
            raise ValueError("plan_id must be None or a non-empty string")

        metadata = self.metadata
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping (or None)")
        # Shallow-copy + read-only: callers cannot mutate a snapshot later.
        object.__setattr__(self, "metadata", MappingProxyType(dict(metadata)))

    @classmethod
    def initial(
        cls,
        session_id: str,
        *,
        intent: Any = None,
        plan_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "SessionState":
        """Create the deterministic first snapshot of a session (version 1)."""
        return cls(
            session_id=session_id,
            version=INITIAL_VERSION,
            intent=intent,
            plan_id=plan_id,
            metadata=dict(metadata) if metadata is not None else {},
        )

    def derive(
        self,
        *,
        intent: Any = _UNSET,
        plan_id: Any = _UNSET,
        metadata: Any = _UNSET,
    ) -> "SessionState":
        """Return the **next** version (``self.version + 1``).

        Unset fields carry over from this snapshot; ``None`` clears
        ``plan_id``/``metadata``. ``metadata`` is replaced (not merged) when
        given. This method deliberately accepts no ``version`` argument:
        versions are minted, never chosen.
        """
        return SessionState(
            session_id=self.session_id,
            version=self.version + 1,
            intent=self.intent if intent is _UNSET else intent,
            plan_id=self.plan_id if plan_id is _UNSET else plan_id,
            metadata=(
                self.metadata if metadata is _UNSET else metadata
            ),
        )
