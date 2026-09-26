"""Slot representation and localized correction for AURA.

The slot system supports:
- extracting slots from user input
- identifying changed slots
- preserving unaffected slots
- detecting missing required slots
- comparing old and new state

The README uses examples such as: origin, destination, date.
"""

from __future__ import annotations

import re as _re
from typing import Any, Dict, FrozenSet, Optional


# ---------------------------------------------------------------------------
# Fixed slot schema per intent
# ---------------------------------------------------------------------------

# For flight_search: these are the known slots. Other intents may have
# different fixed schemas; callers should adapt FIXED_SLOTS per intent.
FIXED_SLOTS: Dict[str, FrozenSet[str]] = {
    "flight_search": frozenset({"origin", "destination", "date"}),
}


class Slots(dict[str, str]):
    """Mutable mapping of slot name → value string.

    Behaves like a dict but provides comparison, diff, and fill-inspection
    utilities used by intent, planner, and fast path.
    """

    def __init__(self, mapping: Optional[Dict[str, str]] = None, **kwargs: Any) -> None:
        super().__init__(mapping or {}, **kwargs)

    def diff(self, other: "Slots") -> Dict[str, Dict[str, str]]:
        """Return {slot: {"old": ..., "new": ...}} for slots that differ.

        Slots present in self but not other, or with different values,
        are included. Unchanged slots are omitted.
        """
        changes: Dict[str, Dict[str, str]] = {}
        all_keys: FrozenSet[str] = self.keys() | other.keys()
        for key in all_keys:
            old = other.get(key)
            new = self.get(key)
            if old != new:
                changes[key] = {"old": old if old is not None else "", "new": new if new is not None else ""}
        return changes

    def preserve(self, other: "Slots") -> "Slots":
        """Return a new Slots containing only keys from *other* that exist in self.

        Used to keep unaffected slots when a correction changes only some.
        """
        preserved: Dict[str, str] = {}
        for key in other:
            if key in self:
                preserved[key] = self[key]
        return Slots(preserved)

    def required_missing(self, required: FrozenSet[str]) -> FrozenSet[str]:
        """Return slots from *required* that are absent from self."""
        missing: FrozenSet[str] = set()
        for key in required:
            if key not in self or self[key] == "":
                missing.add(key)
        return missing


# ---------------------------------------------------------------------------
# Extraction (very lightweight, regex/canonical based; the harness runs
# deterministically so we avoid external LLM calls at runtime)
# ---------------------------------------------------------------------------

CITY_ALIASES: Dict[str, str] = {
    "boston": "Boston",
    "bos": "Boston",
    "new york": "New York",
    "nyc": "New York",
    "chicago": "Chicago",
    "denver": "Denver",
    "seattle": "Seattle",
    "miami": "Miami",
    "austin": "Austin",
}


def _canon_city(text: str) -> Optional[str]:
    """Canonicalise a city name if found in the alias map."""
    lowered = text.lower()
    return CITY_ALIASES.get(lowered)


# Alternation of known city aliases, longest first, so "New York" is
# matched as a whole instead of truncating to "New".
_KNOWN_CITY_ALT = "|".join(sorted(CITY_ALIASES, key=len, reverse=True))


def extract_slots(text: str, intent: str = "flight_search") -> Slots:
    """Extract a Slots dict from *text* for the given *intent*.

    Current supported intent: ``flight_search``

    Extracts: origin, destination, date (free-form).  City names are
    canonicalised; other tokens are kept as-is.

    Returns a Slots instance that may be incomplete — missing required
    slots are indicated by absent keys.
    """
    slots = Slots()

    if intent == "flight_search":
        # --- destination ---
        # Known cities first ("to/make it/change to/switch to/in" +
        # full name): lazy generic captures would stop at the first
        # space and truncate "New York" to "New".
        known = _re.search(
            r"\b(?:make\s+it|change\s+to|switch\s+to|to|in)\s+("
            + _KNOWN_CITY_ALT + r")\b",
            text, _re.I)
        if known:
            slots["destination"] = _canon_city(known.group(1).strip())

        # Generic "to <city>" fallback (keeps unknown cities raw).
        # Pattern is intentionally simple/heuristic because the harness
        # is deterministic and we avoid external LLM calls.
        if "destination" not in slots:
            m = _re.search(r"\bto\s+([A-Za-z\s]+?)(?:\s+for|\s|\?|$)", text, _re.I)
            if m:
                city = _canon_city(m.group(1).strip()) or m.group(1).strip()
                if city:
                    slots["destination"] = city

        # --- destination correction variants ("make it X", "change to X",
        # "switch to X") for unknown cities — known ones matched above.
        if "destination" not in slots:
            mc = _re.search(
                r"\b(?:make\s+it|change\s+to|switch\s+to)\s+"
                r"([A-Za-z][A-Za-z\s]*?)(?:\s+for|\s|\?|$|\.)",
                text, _re.I)
            if mc:
                city = _canon_city(mc.group(1).strip()) or mc.group(1).strip()
                if city:
                    slots["destination"] = city

        # --- origin ---
        # Known cities first (same truncation reason as destination).
        known_o = _re.search(
            r"\bfrom\s+(" + _KNOWN_CITY_ALT + r")\b", text, _re.I)
        if known_o:
            slots["origin"] = _canon_city(known_o.group(1).strip())

        # Look for "from <city>" or "flights from <city>"
        if "origin" not in slots:
            m2 = _re.search(r"\bfrom\s+([A-Za-z\s]+?)(?:\s+for|\s|\?|$)", text, _re.I)
            if m2:
                city = _canon_city(m2.group(1).strip()) or m2.group(1).strip()
                if city:
                    slots["origin"] = city

        # --- date ---
        # Look for "on <date>", "for <date>", "this <day>"
        m3 = _re.search(r"\b(on|for)\s+([A-Za-z0-9\s\-]+?)(?:\s+and|\s|\?|$)", text, _re.I)
        if m3:
            slots["date"] = m3.group(2).strip()

        # --- passenger_name ---
        # Only in a booking context ("book ... for <Name>"): a bare
        # "for <Word>" is usually a date ("for Friday"), not a person.
        if _re.search(r"\bbook\b", text, _re.I):
            mp = _re.search(r"\bfor\s+([A-Z][a-z]+)\s*[\.\?!\s]*$", text)
            if mp:
                slots["passenger_name"] = mp.group(1).strip()
                # The name above is a person, not a date: drop a date
                # value that merely repeats it (e.g. "for Alice").
                if slots.get("date", "").lower() == mp.group(1).strip().lower():
                    del slots["date"]

    return slots


# ---------------------------------------------------------------------------
# Localized comparison / diff
# ---------------------------------------------------------------------------

def compare_states(
    old: Slots, new: Slots, required: FrozenSet[str] = frozenset()
) -> Dict[str, Any]:
    """Compare two Slots instances and return a diff summary.

    Returns:
        {
            "changes": {"slot": {"old": ..., "new": ...}},
            "preserved": {"slot": value, ...},
            "missing_required": [...],
        }
    """
    changes = new.diff(old)

    # Preserved slots: those in old that are unchanged in new
    preserved = old.keys() - set(changes.keys())
    preserved_dict: Dict[str, str] = {k: old[k] for k in preserved if k in old}

    # Missing required slots in the *new* state
    missing = new.required_missing(required) if required else set()

    return {
        "changes": changes,
        "preserved": preserved_dict,
        "missing_required": list(missing),
    }