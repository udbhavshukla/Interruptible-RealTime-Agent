"""Deterministic mock tools convenience layer (wraps MockProvider + registry).

Member 4 can use `make_mock_registry()` for fully deterministic tests.
"""

from __future__ import annotations

from .providers.mock_provider import MockProvider
from .registry import ToolRegistry

__all__ = ["MockProvider", "ToolRegistry", "make_mock_registry"]


def make_mock_registry() -> ToolRegistry:
    return ToolRegistry(provider=MockProvider())
