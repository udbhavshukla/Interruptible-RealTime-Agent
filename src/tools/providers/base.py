"""Provider adapter base: the AURA Tool Contract every provider must honor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProviderResult:
    ok: bool
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    committed: bool = False  # True only after a commit boundary was crossed


class ProviderAdapter:
    """Interface: Mock | Sandbox | Real providers implement this."""

    name = "base"

    async def execute(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        call_id: str,
        cancel_event: asyncio.Event,
    ) -> ProviderResult:
        raise NotImplementedError

    async def cancel(self, call_id: str) -> bool:  # best-effort hook
        return False
