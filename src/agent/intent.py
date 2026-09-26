"""Intent detection for AURA.

Classifies the user's latest utterance into one of:
- new request (flight_search, hotel_search, etc.)
- correction (change a previously stated slot)
- addition (add a new requirement)
- retraction (remove a previous requirement)
- clarification (ask for more info)

The classifier is intentionally lightweight and deterministic — the
harness runs in a mock environment without external LLM access.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Literal, Optional

from .slots import Slots, extract_slots


# ---------------------------------------------------------------------------
# Intent result
# ---------------------------------------------------------------------------

class IntentInfo:
    """Result of intent classification.

    Attributes
    ----------
    type:
        One of "new", "correction", "addition", "retraction", "clarification".
    confidence:
        Float in [0, 1] — always 1.0 for this deterministic classifier.
    slots:
        Slots extracted from the utterance, if any.
    affected_slot:
        If a correction, the slot name being changed (e.g. "destination").
    """

    __slots__ = ("type", "confidence", "slots", "affected_slot")

    def __init__(
        self,
        type: Literal["new", "correction", "addition", "retraction", "clarification"],
        confidence: float,
        slots: Slots,
        affected_slot: Optional[str] = None,
    ) -> None:
        self.type = type
        self.confidence = confidence
        self.slots = slots
        self.affected_slot = affected_slot

    def __repr__(self) -> str:
        return f"IntentInfo(type={self.type!r}, slots={dict(self.slots)})"


# ---------------------------------------------------------------------------
# Keyword-based classification (deterministic, no LLM)
# ---------------------------------------------------------------------------

# Words/phrases that signal a correction of a previous value.
_CORRECTION_TRIGGERS = {
    "actually", "not", "instead", "no", "wait", "rather",
    "meant", "changed", "mistake", "sorry",
}

# Words/phrases that signal a new addition to the request.
_ADDITION_TRIGGERS = {
    "also", "and", "plus", "too", "besides",
    "furthermore", "add", "include",
}

# Words/phrases that signal a retraction of a previous action/requirement.
_RETRACTION_TRIGGERS = {
    "don't", "do not", "stop", "cancel", "forget",
    "remove", "without", "never",
}

# Words/phrases that signal the agent should ask for clarification.
_CLARIFICATION_TRIGGERS = {
    "what", "which", "how", "?", "unclear",
}

# Words/phrases that signal a task/request for the agent to perform.
# A question containing these stays a "new" task, not clarification.
_TASK_REQUEST_TRIGGERS = {
    "find", "search", "book", "flight", "flights", "fly",
}


def _lower(text: str) -> str:
    return text.lower()


def _contains_any(text: str, triggers: FrozenSet[str]) -> bool:
    """Return True if any trigger word appears in *text* (simple token check)."""
    tokens = set(_lower(text).split())
    return bool(tokens & triggers)


def classify_intent(text: str) -> IntentInfo:
    """Classify the user's intent from *text*.

    The classification follows the "Latest Intent Wins" principle:
    the most recent valid intent always wins over older ones.

    Returns
    -------
    IntentInfo
        The classified intent with extracted slots (if any).
    """
    if not text or not text.strip():
        # Empty input — clarification
        slots = Slots()
        return IntentInfo(
            type="clarification",
            confidence=1.0,
            slots=slots,
            affected_slot=None,
        )

    lowered = _lower(text)
    tokens = set(lowered.split())

    # 1. Check for retraction first (highest priority among changes)
    if tokens & _RETRACTION_TRIGGERS:
        # Retraction: user says "don't book it", "forget the hotel", etc.
        # We still extract whatever slots are present so the coordination
        # layer knows what is being retracted.
        slots = extract_slots(text)
        return IntentInfo(
            type="retraction",
            confidence=1.0,
            slots=slots,
            affected_slot=None,
        )

    # 2. Check for correction
    if tokens & _CORRECTION_TRIGGERS:
        slots = extract_slots(text)
        # Determine which slot is affected by looking for city names,
        # dates, etc. in the extracted slots.
        affected: Optional[str] = None
        for slot in slots:
            if slot in {"origin", "destination", "date"}:
                affected = slot
                break
        return IntentInfo(
            type="correction",
            confidence=1.0,
            slots=slots,
            affected_slot=affected,
        )

    # 3. Check for addition — but a task/request (e.g. "find ... and
    # book ...") stays a new request so planning is not blocked.
    if tokens & _ADDITION_TRIGGERS and not (tokens & _TASK_REQUEST_TRIGGERS):
        slots = extract_slots(text)
        # Additions preserve existing intent/slots; the coordination layer
        # merges them.  We just return what was extracted.
        return IntentInfo(
            type="addition",
            confidence=1.0,
            slots=slots,
            affected_slot=None,
        )

    # 4. Check for clarification triggers, but a task/request question stays new
    if tokens & _CLARIFICATION_TRIGGERS or "?" in text:
        if not (tokens & _TASK_REQUEST_TRIGGERS):
            slots = extract_slots(text)
            return IntentInfo(
                type="clarification",
                confidence=1.0,
                slots=slots,
                affected_slot=None,
            )
        # else: task request with "?" falls through to new request below

    # 5. Otherwise: new request
    slots = extract_slots(text)
    return IntentInfo(
        type="new",
        confidence=1.0,
        slots=slots,
        affected_slot=None,
    )