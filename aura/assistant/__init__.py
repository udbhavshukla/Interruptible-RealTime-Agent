"""Conversational assistant layer: turns an accepted tool result into text."""

from __future__ import annotations

from .responder import SYSTEM_PROMPT, AssistantResponder

__all__ = ["AssistantResponder", "SYSTEM_PROMPT"]
