"""Deterministic mock provider: no network, no API keys, reproducible.

Behaviors:
- flight_search/manual_lookup/barcode_lookup: deterministic payloads derived
  from args (stable across runs).
- reserve: reversible, never committed.
- booking: crosses commit boundary quickly; supports fault injection for
  commit-race tests; idempotency is enforced by the REGISTRY (not here), so
  the provider counts real executions via `commit_count` for tests.
- cancel_booking: compensating action.
- fault injection: configure per-tool failures / latencies deterministically.
- cancellation: cooperative via asyncio.Event; read-only tools honor it;
  booking reports committed=True if the boundary was already crossed
  (never fakes a successful cancel after commit).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, Optional

from .base import ProviderAdapter, ProviderResult


def _stable_token(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return h[:12]


class MockProvider(ProviderAdapter):
    name = "mock"

    def __init__(self) -> None:
        self.failures: Dict[str, str] = {}      # tool_name -> error message
        self.latencies: Dict[str, float] = {}   # tool_name -> seconds
        self.commit_count: int = 0              # real booking executions
        self.cancelled_calls: set = set()

    def inject_failure(self, tool_name: str, error: str) -> None:
        self.failures[tool_name] = error

    def clear_failure(self, tool_name: str) -> None:
        self.failures.pop(tool_name, None)

    def set_latency(self, tool_name: str, seconds: float) -> None:
        self.latencies[tool_name] = seconds

    async def execute(self, *, tool_name: str, args: Dict[str, Any],
                      call_id: str, cancel_event: asyncio.Event) -> ProviderResult:
        latency = self.latencies.get(tool_name, 0.0)
        if latency > 0:
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=latency)
                # cancelled during latency window
                self.cancelled_calls.add(call_id)
                return ProviderResult(ok=False, error="cancelled", committed=False)
            except asyncio.TimeoutError:
                pass
        if cancel_event.is_set() and tool_name != "booking":
            self.cancelled_calls.add(call_id)
            return ProviderResult(ok=False, error="cancelled", committed=False)
        if tool_name in self.failures:
            return ProviderResult(ok=False, error=self.failures[tool_name])

        if tool_name == "flight_search":
            o, d, dt = args.get("origin"), args.get("destination"), args.get("date")
            token = _stable_token(str(o), str(d), str(dt))
            return ProviderResult(ok=True, payload={
                "options": [
                    {"option_id": f"opt-{token}-1", "origin": o,
                     "destination": d, "date": dt, "price": 5200},
                    {"option_id": f"opt-{token}-2", "origin": o,
                     "destination": d, "date": dt, "price": 6100},
                ],
                "destination": d,
            })
        if tool_name == "manual_lookup":
            q = args.get("query", "")
            return ProviderResult(ok=True, payload={
                "model": "XYZ-987" if "manual" in str(q) else f"MAN-{_stable_token(str(q))}",
                "source": "manual",
            })
        if tool_name == "barcode_lookup":
            code = args.get("barcode", "")
            return ProviderResult(ok=True, payload={
                "model": f"BAR-{_stable_token(str(code))}", "source": "barcode"})
        if tool_name == "reserve":
            return ProviderResult(ok=True, payload={
                "reservation_id": f"res-{_stable_token(args.get('option_id', ''))}",
                "reversible": True}, committed=False)
        if tool_name == "booking":
            if cancel_event.is_set():
                # Boundary already crossed in this deterministic mock:
                # truthfully report committed rather than fake-cancel.
                self.commit_count += 1
                return ProviderResult(ok=False, error="cancel_after_commit",
                                      committed=True)
            self.commit_count += 1
            return ProviderResult(ok=True, payload={
                "booking_id": f"bkg-{_stable_token(args.get('option_id', ''), args.get('passenger', ''))}",
                "option_id": args.get("option_id"),
                "passenger": args.get("passenger"),
            }, committed=True)
        if tool_name == "cancel_booking":
            return ProviderResult(ok=True, payload={
                "cancelled_booking": args.get("booking_id"),
                "compensated": True}, committed=True)
        return ProviderResult(ok=False, error=f"mock: unknown tool '{tool_name}'")

    async def cancel(self, call_id: str) -> bool:
        self.cancelled_calls.add(call_id)
        return True
