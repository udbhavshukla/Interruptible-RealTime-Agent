"""Member 1 — AI Agent / LLM Engineer interfaces for AURA.

Exports the core reasoning primitives that ParticipantAgent uses
for intent detection, slot management, planning, and fast-path acks.
All components are plain Python (no pydantic, no external deps)
so they work within the harness's deterministic mock environment.
"""

from __future__ import annotations

from .intent import classify_intent, IntentInfo
from .slots import extract_slots, compare_states, Slots
from .planner import make_plan, replan, Plan
from .fastpath import generate_ack

__all__ = [
    "IntentInfo",
    "Slots",
    "Plan",
    "classify_intent",
    "extract_slots",
    "compare_states",
    "make_plan",
    "replan",
    "generate_ack",
]