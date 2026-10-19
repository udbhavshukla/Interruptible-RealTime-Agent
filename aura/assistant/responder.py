"""Assistant responder: model-backed answer with a deterministic fallback.

The responder sits *after* AURA's acceptance gate. It is only ever asked to
phrase a result the gate already accepted, so a stale or cancelled tool run
can never reach the model. The caller still owns the final version check
(see ``aura/runtime/websocket_server.py``): it must confirm the result's
version is still current after the model returns, because generation is not
instantaneous and a newer intent may have arrived meanwhile.

Without a provider the responder returns the runtime's deterministic answer,
so the product works with no credentials and every test runs offline.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable, Optional

from aura.llm.base import LLMError, LLMProvider, Message

__all__ = ["AssistantResponder", "SYSTEM_PROMPT"]

_DEFAULT_FALLBACK_TEXT = "Here is the latest result."

SYSTEM_PROMPT = (
    "You are AURA, a real-time assistant for an interruptible flight-search "
    "demo. Answer only the user's current intent; ignore earlier, superseded "
    "requests. The flight/travel data is simulated, so never imply a real "
    "booking or charge occurred. Be concise — one or two sentences."
)


class AssistantResponder:
    """Phrases an accepted tool result, with graceful degradation."""

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        *,
        fallback: Optional[Callable[[Any], str]] = None,
        timeout_s: float = 30.0,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._fallback = fallback
        self._timeout_s = float(timeout_s)
        self._system_prompt = system_prompt

    @property
    def available(self) -> bool:
        """True when a real provider is wired in."""
        return self._provider is not None

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "none")

    # -- prompt construction ----------------------------------------------
    def _user_prompt(self, intent: str, tool_result: Any, state_version: int) -> str:
        try:
            result_json = json.dumps(tool_result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            result_json = str(tool_result)
        return (
            f"Current intent: {intent or 'unknown'}\n"
            f"Accepted tool result (version {state_version}): {result_json}\n"
            "Write the reply to the user for this intent."
        )

    def _fallback_text(self, tool_result: Any) -> str:
        if self._fallback is not None:
            try:
                text = self._fallback(tool_result)
            except Exception:  # fallback must never raise
                text = None
            if isinstance(text, str) and text.strip():
                return text.strip()
        return _DEFAULT_FALLBACK_TEXT

    # -- model calls -------------------------------------------------------
    async def respond(
        self, *, intent: str, tool_result: Any, state_version: int
    ) -> str:
        """Return the assistant text, falling back deterministically."""
        if self._provider is None:
            return self._fallback_text(tool_result)
        messages = [
            Message("system", self._system_prompt),
            Message("user", self._user_prompt(intent, tool_result, state_version)),
        ]
        try:
            text = await self._provider.complete(messages, timeout=self._timeout_s)
        except LLMError:
            return self._fallback_text(tool_result)
        text = (text or "").strip()
        return text or self._fallback_text(tool_result)

    async def stream(
        self, *, intent: str, tool_result: Any, state_version: int
    ) -> AsyncIterator[str]:
        """Yield answer deltas; falls back to one whole-text delta."""
        if self._provider is None:
            yield self._fallback_text(tool_result)
            return
        messages = [
            Message("system", self._system_prompt),
            Message("user", self._user_prompt(intent, tool_result, state_version)),
        ]
        emitted = False
        try:
            async for delta in self._provider.stream(messages, timeout=self._timeout_s):
                if delta:
                    emitted = True
                    yield delta
        except LLMError:
            if not emitted:
                yield self._fallback_text(tool_result)
