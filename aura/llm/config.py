"""Server-side provider configuration.

Configuration always comes from the environment, never from the frontend:
API keys must never reach the browser. :meth:`ProviderConfig.redacted`
returns a log-safe view.

Recognised variables
--------------------
======================  ==================================================
``AURA_LLM_PROVIDER``   ``none`` (default), ``mock`` or ``openai``.
``AURA_LLM_BASE_URL``   OpenAI-compatible base URL, e.g.
                        ``https://api.openai.com/v1`` or a local gateway.
``AURA_LLM_API_KEY``    Bearer token for the provider. Required for
                        ``openai``; never logged.
``AURA_LLM_MODEL``      Model identifier, e.g. ``gpt-4o-mini``.
``AURA_LLM_TIMEOUT_S``  Per-call timeout in seconds (default ``30``).
======================  ==================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

__all__ = ["ProviderConfig", "VALID_PROVIDERS"]

VALID_PROVIDERS = ("none", "mock", "openai")


def _parse_timeout(raw: Optional[str], default: float) -> float:
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ProviderConfig:
    """Immutable provider settings resolved from the environment."""

    provider: str = "none"
    base_url: str = "https://api.openai.com/v1"
    api_key: Optional[str] = None
    model: str = "gpt-4o-mini"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"provider must be one of {VALID_PROVIDERS}, got {self.provider!r}"
            )

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ProviderConfig":
        source = os.environ if env is None else env
        provider = (source.get("AURA_LLM_PROVIDER") or "none").strip().lower()
        if provider not in VALID_PROVIDERS:
            provider = "none"
        key = source.get("AURA_LLM_API_KEY")
        return cls(
            provider=provider,
            base_url=(source.get("AURA_LLM_BASE_URL") or cls.base_url).strip(),
            api_key=key.strip() if isinstance(key, str) and key.strip() else None,
            model=(source.get("AURA_LLM_MODEL") or cls.model).strip(),
            timeout_s=_parse_timeout(source.get("AURA_LLM_TIMEOUT_S"), cls.timeout_s),
        )

    @property
    def enabled(self) -> bool:
        """True when a real provider is configured (not ``none``)."""
        return self.provider != "none"

    def redacted(self) -> dict:
        """Log-safe representation; the API key is never revealed."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_s": self.timeout_s,
            "api_key": "***" if self.api_key else None,
        }
