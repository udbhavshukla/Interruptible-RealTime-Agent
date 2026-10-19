"""Responder tests: model path, offline fallback, and failure degradation."""

from __future__ import annotations

import asyncio
from typing import Optional, Sequence

from aura.assistant.responder import AssistantResponder
from aura.llm.base import LLMError, Message, ProviderUnavailable

RESULT = {"origin": "Mumbai", "destination": "Delhi", "options": [{"price": "₹5,400"}]}


async def _collect(aiter) -> list:
    return [item async for item in aiter]


class _Provider:
    name = "fake"

    def __init__(self, text: Optional[str] = None, error: Optional[Exception] = None):
        self.text = text
        self.error = error
        self.calls: list[Sequence[Message]] = []

    async def complete(self, messages, *, timeout=None):
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self.text if self.text is not None else ""

    async def stream(self, messages, *, timeout=None):
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        for piece in ("a", "b", "c"):
            yield piece


def _fallback(result) -> str:
    return f"offline:{result.get('destination')}"


def _respond(responder, *, version: int = 1):
    return asyncio.run(
        responder.respond(intent="fly", tool_result=RESULT, state_version=version)
    )


def test_no_provider_returns_deterministic_fallback():
    responder = AssistantResponder(fallback=_fallback)
    assert responder.available is False
    assert _respond(responder) == "offline:Delhi"


def test_model_text_is_used_when_available():
    provider = _Provider("Model says hi.")
    responder = AssistantResponder(provider, fallback=_fallback)
    out = _respond(responder, version=3)
    assert out == "Model says hi."
    prompt = provider.calls[0][-1].content
    assert "fly" in prompt
    assert "version 3" in prompt
    assert "Delhi" in prompt


def test_unreachable_provider_degrades_to_fallback():
    responder = AssistantResponder(
        _Provider(error=ProviderUnavailable("down")), fallback=_fallback
    )
    assert _respond(responder) == "offline:Delhi"


def test_generic_llm_error_degrades_to_fallback():
    responder = AssistantResponder(_Provider(error=LLMError("bad")), fallback=_fallback)
    assert _respond(responder) == "offline:Delhi"


def test_empty_model_answer_degrades_to_fallback():
    responder = AssistantResponder(_Provider("   "), fallback=_fallback)
    assert _respond(responder) == "offline:Delhi"


def test_broken_fallback_never_raises():
    def boom(_):
        raise RuntimeError("fallback bug")

    responder = AssistantResponder(None, fallback=boom)
    out = _respond(responder)
    assert isinstance(out, str) and out.strip()


def test_stream_forwards_deltas():
    responder = AssistantResponder(_Provider(), fallback=_fallback)
    chunks = asyncio.run(
        _collect(responder.stream(intent="fly", tool_result=RESULT, state_version=1))
    )
    assert "".join(chunks) == "abc"


def test_stream_offline_yields_fallback_once():
    responder = AssistantResponder(None, fallback=_fallback)
    chunks = asyncio.run(
        _collect(responder.stream(intent="fly", tool_result=RESULT, state_version=1))
    )
    assert chunks == ["offline:Delhi"]


def test_stream_degrades_when_provider_fails_before_any_output():
    responder = AssistantResponder(_Provider(error=LLMError("bad")), fallback=_fallback)
    chunks = asyncio.run(
        _collect(responder.stream(intent="fly", tool_result=RESULT, state_version=1))
    )
    assert chunks == ["offline:Delhi"]
