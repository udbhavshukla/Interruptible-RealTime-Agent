"""Provider-agnostic language-model interface.

The runtime never depends on a specific vendor. A provider is any object
implementing :class:`LLMProvider`: it accepts an ordered list of
:class:`Message` objects and returns text, either in one shot
(``complete``) or incrementally (``stream``).

Error taxonomy
--------------
- :class:`ProviderUnavailable` — the provider could not be reached or the
  credentials were rejected. Callers should degrade to a deterministic
  fallback rather than surfacing a stack trace.
- :class:`LLMError` — the provider responded but the answer was unusable
  (HTTP error, malformed body, empty content, timeout).

Both are subclasses of :class:`LLMError` so a single ``except LLMError``
handles every provider failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol, Sequence, runtime_checkable

__all__ = [
    "LLMError",
    "ProviderUnavailable",
    "Message",
    "LLMProvider",
]

_VALID_ROLES = frozenset({"system", "user", "assistant"})


class LLMError(RuntimeError):
    """A model call failed in a way callers are expected to handle."""


class ProviderUnavailable(LLMError):
    """The provider is unreachable or rejected the credentials."""


@dataclass(frozen=True)
class Message:
    """One chat message. ``role`` is ``system``, ``user`` or ``assistant``."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_ROLES)}, got {self.role!r}"
            )
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal async chat interface implemented by every provider."""

    name: str

    async def complete(
        self, messages: Sequence[Message], *, timeout: Optional[float] = None
    ) -> str:
        """Return the assistant reply as one string."""
        ...

    def stream(
        self, messages: Sequence[Message], *, timeout: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Yield the reply incrementally (text deltas)."""
        ...
