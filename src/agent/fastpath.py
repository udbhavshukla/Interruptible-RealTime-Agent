"""Fast Path — immediate acknowledgements for AURA.

The Fast Path must be extremely lightweight:
- Respond in 200–800 ms
- Never block on Slow Path or tool calls
- Never claim completion ("done", "booked", etc.)
- Only acknowledge: request received, interruption understood,
  correction preserved, clarification needed

Responses are short template strings.  No LLM calls, no tool calls,
no external dependencies.
"""

from __future__ import annotations

from typing import Optional

from .slots import Slots
from .intent import IntentInfo


# ---------------------------------------------------------------------------
# Fast-path ACK templates
# ---------------------------------------------------------------------------

#: Acknowledge a new flight-search request.
ACK_NEW_REQUEST = "Sure — I'm checking flights {origin_from} to {destination}."

#: Acknowledge an interruption / correction.
ACK_INTERRUPTION = "Got it — switching the {affected_slot} to {new_value}."

#: Acknowledge that existing information is preserved.
ACK_PRESERVE = "I've kept {preserved_items}."

#: A clarification request when info is missing or ambiguous.
ACK_CLARIFICATION = "Just to confirm — {question}"

#: Generic ACK when nothing specific can be said yet.
ACK_ACK = "One moment please."


def _format_slot(value: Optional[str]) -> str:
    """Render a slot value for a template; empty string if None."""
    return value if value else ""


def generate_ack(
    intent: IntentInfo,
    interrupt: bool = False,
) -> str:
    """Generate a fast-path acknowledgement for the given *intent*.

    Parameters
    ----------
    intent:
        The IntentInfo result from :func:`intent.classify_intent`.
    interrupt:
        If True, the user interrupted (correction/addition/retraction).
        If False, this is a new request.

    Returns
    -------
    str
        A short ACK text (< 120 characters) suitable for immediate
        spoken/filler response.
    """
    if intent.type == "new":
        return _ack_new_request(intent.slots)

    if intent.type == "correction":
        return _ack_correction(intent.slots, interrupt=interrupt)

    if intent.type == "addition":
        return _ack_addition(intent.slots)

    if intent.type == "retraction":
        return _ack_retraction(intent.slots)

    if intent.type == "clarification":
        return _ack_clarification(intent.slots)

    return ACK_ACK


def _ack_new_request(slots: Slots) -> str:
    origin = _format_slot(slots.get("origin"))
    dest = _format_slot(slots.get("destination"))
    parts: list[str] = []
    if origin:
        parts.append(f"from {origin}")
    if dest:
        parts.append(f"to {dest}")
    if not parts:
        return ACK_ACK
    middle = " ".join(parts)
    return f"Sure — I'll check {middle}."


def _ack_correction(slots: Slots, interrupt: bool = False) -> str:
    """Generate ACK for a correction (interruption).

    The *interrupt* flag is kept for compatibility with the agent loop
    but the template focuses on the new value.
    """
    # Look for the changed slot value
    new_dest = _format_slot(slots.get("destination"))
    new_orig = _format_slot(slots.get("origin"))

    if new_dest:
        return ACK_INTERRUPTION.format(
            affected_slot="destination",
            new_value=new_dest,
        )
    if new_orig:
        return ACK_INTERRUPTION.format(
            affected_slot="origin",
            new_value=new_orig,
        )
    # Fallback
    return ACK_ACK


def _ack_addition(slots: Slots) -> str:
    """ACK for an addition (e.g. 'also find a hotel')."""
    # If we have a destination, acknowledge it's preserved
    dest = _format_slot(slots.get("destination"))
    if dest:
        return f"Got it — I'll also look for options in {dest}."
    return ACK_ACK


def _ack_retraction(slots: Slots) -> str:
    """ACK for a retraction (e.g. 'don't book the hotel')."""
    return ACK_ACK


def _ack_clarification(slots: Slots) -> str:
    """ACK when we need more info from the user."""
    # If we have a partial destination, reference it
    dest = _format_slot(slots.get("destination"))
    if dest:
        return ACK_CLARIFICATION.format(question=f"Is the destination {dest}?")
    return ACK_CLARIFICATION.format(question="Could you clarify?")


# ---------------------------------------------------------------------------
# Interruption-aware ACK helper
# ---------------------------------------------------------------------------

def ack_for_interruption(
    old_slots: Slots,
    new_slots: Slots,
    affected_slot: str,
) -> str:
    """Produce a fast-path ACK when the user interrupts mid-task.

    This is called by the coordination layer when an INTERRUPT event
    arrives.  It compares old vs new state and produces a content-aware
    acknowledgement that acknowledges the correction without claiming
    completion.
    """
    new_val = _format_slot(new_slots.get(affected_slot))
    old_val = _format_slot(old_slots.get(affected_slot))

    if new_val:
        return ACK_INTERRUPTION.format(
            affected_slot=affected_slot,
            new_value=new_val,
        )
    # Fallback if we can't determine the new value
    return ACK_ACK