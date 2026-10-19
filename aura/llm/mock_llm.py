"""Deterministic in-process provider.

Used for tests and for ``AURA_LLM_PROVIDER=mock``, so the full conversation
path (server -> responder -> provider -> answer) can run with no credentials
and no network. It never claims to be a real model: replies are prefixed so
an operator can tell them apart from a live provider at a glance.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from .base import Message

__all__ = ["MockLLMProvider"]


class MockLLMProvider:
    """Echo-style provider with an optional custom ``reply`` callable."""

    name = "mock"

    def __init__(self, reply: Optional[Callable[[Sequence[Message]], str]] = None) -> None:
        self._reply = reply

    def _compose(self, messages: Sequence[Message]) -> str:
        if self._reply is not None:
            return self._reply(messages)
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        return f"[mock] {last_user}".strip()

    async def complete(
        self, messages: Sequence[Message], *, timeout: Optional[float] = None
    ) -> str:
        return self._compose(messages)

    async def stream(self, messages: Sequence[Message], *, timeout: Optional[float] = None):
        text = self._compose(messages)
        for i, token in enumerate(text.split(" ")):
            yield ("" if i == 0 else " ") + token
