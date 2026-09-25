"""Grounding: candidate facts -> structured proposals for state layer.

Output makes explicit: what was perceived, confidence, source, verified?,
safe-to-commit? Only verified (or explicit user-accepted) proposals may
become trusted state. Low-confidence / unverified proposals NEVER silently
overwrite confirmed slots -- the caller must resolve clarification/conflict.

Version binding (R3 fix): each proposal records the session state_version
at ground time. apply_verified() only commits proposals bound to the
session's CURRENT version; older (or future) proposals are reported in
`skipped` with a stale reason -- a verified-but-obsolete perception can
never overwrite newer state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

from .verification import Evidence, VerificationOutcome, verify_slot


@dataclass(frozen=True)
class GroundedProposal:
    slot: str
    candidate_value: str
    confidence: float
    source: str
    verified: bool
    safe_to_commit: bool
    reason: str = ""
    # Session state_version at ground time. None = unbound (legacy /
    # callers without state access); unbound proposals skip the version
    # gate but still require verified/safe_to_commit.
    state_version: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot": self.slot,
            "candidate_value": self.candidate_value,
            "confidence": self.confidence,
            "source": self.source,
            "verified": self.verified,
            "safe_to_commit": self.safe_to_commit,
            "reason": self.reason,
            "state_version": self.state_version,
        }


def ground_facts(facts: Dict[str, str], *, confidence: float, source: str,
                 threshold: float = 0.75,
                 state_version: Optional[int] = None) -> List[GroundedProposal]:
    """Ground one perception batch into per-slot proposals.

    Pass a trusted source ("tool", "manual", "barcode", "user", "api") for
    corroborated facts, or a perceptual source ("vision", "image", "audio")
    for raw perception. Only `verified` proposals are safe to commit.

    Pass `state_version` (e.g. `state.current_version(session_id)`) to bind
    each proposal to the session version it was grounded against; stale
    proposals are then rejected by apply_verified() instead of overwriting
    newer state.
    """
    proposals: List[GroundedProposal] = []
    for slot, value in facts.items():
        outcome: VerificationOutcome = verify_slot(
            [Evidence(slot=slot, value=str(value),
                      confidence=confidence, source=source)],
            threshold=threshold)
        verified = outcome.decision == "verified"
        proposals.append(GroundedProposal(
            slot=slot, candidate_value=str(value), confidence=confidence,
            source=source, verified=verified,
            safe_to_commit=verified,
            reason=outcome.reason or outcome.decision,
            state_version=state_version))
    return proposals


def filter_committable(proposals: List[GroundedProposal]) -> List[GroundedProposal]:
    # Commit point enforces BOTH flags: safe_to_commit alone is not enough.
    # ground_facts() always sets them jointly, so honest flows are
    # unaffected; hand-crafted divergent proposals can never slip through.
    return [p for p in proposals if p.safe_to_commit and p.verified]


def apply_verified(state_manager, session_id: str,
                   proposals: List[GroundedProposal]):
    """Apply only safe-to-commit proposals to state. Returns (snap, skipped).

    A committable proposal is applied ONLY if it is bound to the session's
    current version (proposal.state_version is None or equals current).
    Version-bound proposals from any other version are NOT applied and are
    returned in `skipped` with a "stale_version_..." reason -- never
    silently discarded, never overwriting newer state.

    Raises no error for empty input; unverified proposals are skipped
    (returned in `skipped`). Unknown slots raise InvalidSlotError from the
    state manager -- never silently inserted.
    """
    current = state_manager.current_version(session_id)
    committable_flag = lambda p: p.safe_to_commit and p.verified
    skipped = [p for p in proposals if not committable_flag(p)]
    committable = []
    for p in filter_committable(proposals):
        if p.state_version is not None and p.state_version != current:
            skipped.append(replace(
                p, reason=f"stale_version_{p.state_version}_vs_current_{current}"))
        else:
            committable.append(p)
    snap = None
    if committable:
        snap = state_manager.update_slots(
            session_id, {p.slot: p.candidate_value for p in committable})
    else:
        snap = state_manager.get_state(session_id)
    return snap, skipped
