"""Model-provider layer for AURA.

AURA's runtime coordinates *tools*; this package owns the optional language
model that turns an accepted result into a conversational answer. Nothing
here imports the runtime, so the provider layer stays independently testable.

Public surface
--------------
- :class:`~aura.llm.base.Message`, :class:`~aura.llm.base.LLMProvider`
- :class:`~aura.llm.base.LLMError`, :class:`~aura.llm.base.ProviderUnavailable`
- :class:`~aura.llm.config.ProviderConfig`
- :class:`~aura.llm.mock_llm.MockLLMProvider`
- :class:`~aura.llm.openai_compatible.OpenAICompatibleProvider`
"""

from __future__ import annotations

from .base import LLMError, LLMProvider, Message, ProviderUnavailable
from .config import ProviderConfig
from .mock_llm import MockLLMProvider
from .openai_compatible import OpenAICompatibleProvider

__all__ = [
    "LLMError",
    "LLMProvider",
    "Message",
    "ProviderUnavailable",
    "ProviderConfig",
    "MockLLMProvider",
    "OpenAICompatibleProvider",
]
