"""Verification: PERCEPTION PROPOSES, VERIFICATION COMMITS.

Rules:
- confidence < threshold -> clarification request (never commit).
- two sources disagree on the same slot -> conflict (never silently pick).
- a trusted source (tool/manual/barcode/explicit user correction) above
  threshold verifies the fact.
- unverified candidates must NOT overwrite trusted state (grounding enforces).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_THRESHOLD = 0.75
TRUSTED_SOURCES = frozenset({"tool", "manual", "barcode", "user", "api"})


@dataclass(frozen=True)
class Evidence:
    slot: str
    value: str
    confidence: float
    source: str  # vision|audio|tool|manual|barcode|user|api


@dataclass(frozen=True)
class VerificationOutcome:
    decision: str  # verified|clarification|conflict
    slot: Optional[str] = None
    value: Optional[str] = None
    confidence: Optional[float] = None
    reason: str = ""
    conflicting: List[Dict[str, Any]] = field(default_factory=list)


def verify_slot(evidences: List[Evidence],
                threshold: float = DEFAULT_THRESHOLD) -> VerificationOutcome:
    """Verify one slot from one or more evidence items (same slot)."""
    if not evidences:
        return VerificationOutcome(decision="clarification",
                                   reason="no_evidence")
    slots = {e.slot for e in evidences}
    if len(slots) != 1:
        return VerificationOutcome(
            decision="conflict", reason="mixed_slots",
            conflicting=[e.__dict__ for e in evidences])
    slot = evidences[0].slot
    by_value: Dict[str, List[Evidence]] = {}
    for e in evidences:
        by_value.setdefault(e.value, []).append(e)
    if len(by_value) > 1:
        return VerificationOutcome(
            decision="conflict", slot=slot,
            reason="conflicting_values",
            conflicting=[{"value": v, "confidence": max(x.confidence for x in es),
                           "sources": sorted({x.source for x in es})}
                          for v, es in by_value.items()])
    value = next(iter(by_value))
    best = max(evidences, key=lambda e: e.confidence)
    trusted = any(e.source in TRUSTED_SOURCES and e.confidence >= threshold
                  for e in evidences)
    if best.confidence >= threshold:
        # Above-threshold evidence verifies. A trusted corroborating source
        # is the strongest path, but a single high-confidence perceptual
        # source also verifies (it still went through this explicit gate --
        # perception never auto-commits; grounding only commits `verified`
        # proposals via apply_verified).
        return VerificationOutcome(
            decision="verified", slot=slot, value=value,
            confidence=best.confidence,
            reason="trusted_source" if trusted else "above_threshold")
    return VerificationOutcome(
        decision="clarification", slot=slot, value=value,
        confidence=best.confidence,
        reason=f"low_confidence_{best.confidence:.2f}_below_{threshold:.2f}")


def needs_clarification(outcome: VerificationOutcome) -> bool:
    return outcome.decision == "clarification"


def is_conflict(outcome: VerificationOutcome) -> bool:
    return outcome.decision == "conflict"
