"""Schema-driven Tool Registry.

Responsibilities:
- register / lookup tools + manifest retrieval + manifest validation
- argument validation against manifest args_schema/required_args
- provider selection (mock default; sandbox/real injectable)
- async execution through adapters with per-call cancel events
- call lifecycle metadata (pending->running->completed|failed|cancelled|committed)
- cancellation hooks (cooperative; truthful after commit boundary)
- idempotency store for state-changing tools: same key -> same result,
  effect executes exactly once (in-memory, session-scoped).

Deterministic: unique call_ids enforced; mock outputs deterministic.
"""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .manifests.definitions import TOOL_MANIFESTS
from .providers.base import ProviderAdapter, ProviderResult
from .providers.mock_provider import MockProvider


# ---------------------------------------------------------------------------
# Errors (structured for runtime/eval layer)
# ---------------------------------------------------------------------------

class ToolError(Exception):
    pass


class UnknownToolError(ToolError):
    pass


class InvalidManifestError(ToolError):
    pass


class InvalidArgumentsError(ToolError):
    pass


class DuplicateCallIdError(ToolError):
    pass


class UnknownCallIdError(ToolError):
    pass


REQUIRED_MANIFEST_KEYS = (
    "name", "kind", "version", "args_schema", "timeout_s",
    "cancellable", "reversibility",
)
VALID_KINDS = ("read_only", "preparatory", "state_modifying", "compensating")


def validate_manifest(m: Dict[str, Any]) -> None:
    for k in REQUIRED_MANIFEST_KEYS:
        if k not in m:
            raise InvalidManifestError(f"Manifest missing key '{k}'")
    if m["kind"] not in VALID_KINDS:
        raise InvalidManifestError(f"Invalid kind '{m['kind']}'")
    if not isinstance(m["args_schema"], dict):
        raise InvalidManifestError("args_schema must be a dict")
    if m.get("commit_boundary") and m["kind"] not in ("state_modifying", "compensating"):
        raise InvalidManifestError("commit_boundary only for state_modifying/compensating")
    idem = m.get("idempotency")
    if m["kind"] in ("state_modifying", "compensating") and not idem:
        raise InvalidManifestError(f"{m['name']}: state-changing tools require idempotency policy")


def validate_args(manifest: Dict[str, Any], args: Dict[str, Any]) -> None:
    schema = manifest.get("args_schema", {})
    for k in args:
        if k not in schema:
            raise InvalidArgumentsError(f"Unknown argument '{k}' for tool '{manifest['name']}'")
    for k in manifest.get("required_args", []):
        if k not in args:
            raise InvalidArgumentsError(f"Missing required argument '{k}'")


@dataclass
class ToolCall:
    call_id: str
    tool_name: str
    session_id: str
    state_version: int
    args: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    idempotency_key: Optional[str] = None
    committed: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)


class ToolRegistry:
    def __init__(self, provider: Optional[ProviderAdapter] = None) -> None:
        self._manifests: Dict[str, Dict[str, Any]] = copy.deepcopy(TOOL_MANIFESTS)
        for m in self._manifests.values():
            validate_manifest(m)
        self._provider: ProviderAdapter = provider or MockProvider()
        self._calls: Dict[str, ToolCall] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}
        # idempotency: (session_id, tool_name, key) -> stored result dict
        self._idempotency: Dict[str, Dict[str, Any]] = {}
        # Per-key locks serializing concurrent same-key executions so the
        # second arrival rechecks the store instead of double-executing.
        # Created via synchronous setdefault (atomic on a single event
        # loop); registries are single-loop scoped like the tests.
        self._key_locks: Dict[str, asyncio.Lock] = {}

    # -- registration / lookup ----------------------------------------
    def register_tool(self, manifest: Dict[str, Any],
                      handler: Optional[Callable] = None) -> None:
        """Register (or override) a tool. Handler reserved for future sync
        providers; current execution goes through the provider adapter."""
        validate_manifest(manifest)
        self._manifests[manifest["name"]] = copy.deepcopy(manifest)

    def lookup(self, tool_name: str) -> Dict[str, Any]:
        try:
            return copy.deepcopy(self._manifests[tool_name])
        except KeyError:
            raise UnknownToolError(f"Unknown tool '{tool_name}'") from None

    def get_manifest(self, tool_name: str) -> Dict[str, Any]:
        return self.lookup(tool_name)

    def list_tools(self):
        return sorted(self._manifests.keys())

    def set_provider(self, provider: ProviderAdapter) -> None:
        self._provider = provider

    # -- idempotency ----------------------------------------------------
    @staticmethod
    def _idem_scope_key(session_id: str, tool_name: str, key: str) -> str:
        return f"{session_id}|{tool_name}|{key}"

    def _extract_idem_key(self, manifest: Dict[str, Any],
                          args: Dict[str, Any]) -> Optional[str]:
        policy = manifest.get("idempotency")
        if not policy:
            return None
        return args.get(policy["key_field"])

    # -- execution ------------------------------------------------------
    def _new_call(self, *, call_id: str, tool_name: str, session_id: str,
                  state_version: int, args: Dict[str, Any],
                  idempotency_key: Optional[str]) -> ToolCall:
        if call_id in self._calls:
            raise DuplicateCallIdError(f"Duplicate call_id '{call_id}'")
        call = ToolCall(call_id=call_id, tool_name=tool_name,
                        session_id=session_id, state_version=int(state_version),
                        args=copy.deepcopy(args), idempotency_key=idempotency_key)
        self._calls[call_id] = call
        return call

    async def execute(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        call_id: str,
        session_id: str,
        state_version: int,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a tool call. Returns a result envelope::

            {"call_id","tool_name","session_id","state_version","status",
             "payload"|"error", "committed", "idempotent_replay": bool}

        State-changing tools with a repeated idempotency key return the
        ORIGINAL stored result with idempotent_replay=True and do NOT
        re-execute the effect. Concurrent same-key executions are
        serialized per key: exactly one executes, the rest replay.
        """
        manifest = self.lookup(tool_name)  # raises UnknownToolError
        validate_args(manifest, args)
        idem_key = self._extract_idem_key(manifest, args)
        idem_store_key = (
            self._idem_scope_key(session_id, tool_name, idem_key)
            if idem_key else None
        )
        if idem_store_key is None:
            return await self._run_call(
                tool_name=tool_name, manifest=manifest, args=args,
                call_id=call_id, session_id=session_id,
                state_version=state_version, timeout_s=timeout_s,
                idem_key=None, idem_store_key=None)
        lock = self._key_locks.setdefault(idem_store_key, asyncio.Lock())
        async with lock:
            return await self._run_call(
                tool_name=tool_name, manifest=manifest, args=args,
                call_id=call_id, session_id=session_id,
                state_version=state_version, timeout_s=timeout_s,
                idem_key=idem_key, idem_store_key=idem_store_key)

    async def _run_call(
        self,
        *,
        tool_name: str,
        manifest: Dict[str, Any],
        args: Dict[str, Any],
        call_id: str,
        session_id: str,
        state_version: int,
        timeout_s: Optional[float],
        idem_key: Optional[str],
        idem_store_key: Optional[str],
    ) -> Dict[str, Any]:
        """Single execution attempt; caller holds the per-key lock if any."""
        # Idempotency short-circuit BEFORE creating a new lifecycle entry?
        # We still create the call record (traceable) but mark replay.
        if idem_store_key and idem_store_key in self._idempotency:
            stored = copy.deepcopy(self._idempotency[idem_store_key])
            replay_call = self._new_call(
                call_id=call_id, tool_name=tool_name, session_id=session_id,
                state_version=state_version, args=args, idempotency_key=idem_key)
            replay_call.status = stored["status"]
            replay_call.committed = stored.get("committed", False)
            replay_call.result = copy.deepcopy(stored.get("payload"))
            replay_call.updated_ts = time.time()
            out = copy.deepcopy(stored)
            out.update({"call_id": call_id, "idempotent_replay": True})
            return out

        call = self._new_call(
            call_id=call_id, tool_name=tool_name, session_id=session_id,
            state_version=state_version, args=args, idempotency_key=idem_key)
        call.status = "running"
        cancel_event = asyncio.Event()
        self._cancel_events[call_id] = cancel_event
        try:
            coro = self._provider.execute(
                tool_name=tool_name, args=copy.deepcopy(args),
                call_id=call_id, cancel_event=cancel_event)
            ts = timeout_s if timeout_s is not None else manifest.get("timeout_s", 20)
            res: ProviderResult = await asyncio.wait_for(coro, timeout=ts)
        except asyncio.TimeoutError:
            call.status = "failed"
            call.error = "timeout"
            call.updated_ts = time.time()
            return self._envelope(call, idempotent_replay=False)
        except asyncio.CancelledError:
            call.status = "cancelled"
            call.updated_ts = time.time()
            return self._envelope(call, idempotent_replay=False)
        finally:
            self._cancel_events.pop(call_id, None)

        if res.ok:
            call.status = "committed" if res.committed else "completed"
            call.committed = res.committed
            call.result = copy.deepcopy(res.payload)
        else:
            # Truthful commit-boundary reporting: provider sets committed=True
            # when cancellation arrived after the boundary.
            call.committed = res.committed
            call.status = "committed" if res.committed else (
                "cancelled" if res.error == "cancelled" else "failed")
            call.error = res.error
        call.updated_ts = time.time()

        envelope = self._envelope(call, idempotent_replay=False)
        # Store idempotent result only for successful committed/state-changing
        # effects AND for replayable reads? Spec: store state-changing effects.
        if idem_store_key and call.status in ("committed", "completed") and res.ok:
            stored = copy.deepcopy(envelope)
            stored.pop("call_id", None)
            self._idempotency[idem_store_key] = stored
        return envelope

    @staticmethod
    def _envelope(call: ToolCall, idempotent_replay: bool) -> Dict[str, Any]:
        env: Dict[str, Any] = {
            "call_id": call.call_id,
            "tool_name": call.tool_name,
            "session_id": call.session_id,
            "state_version": call.state_version,
            "status": call.status,
            "committed": call.committed,
            "idempotent_replay": idempotent_replay,
        }
        if call.result is not None:
            env["payload"] = copy.deepcopy(call.result)
        if call.error is not None:
            env["error"] = call.error
        return env

    # -- cancellation / introspection (for Member 2 runtime) -------------
    async def cancel_call(self, call_id: str) -> Dict[str, Any]:
        """Cooperative cancel. Never fakes success after commit.

        Returns {"call_id","cancelled": bool, "committed": bool, "status"}.
        If the call already committed, cancelled=False, committed=True.
        """
        call = self._calls.get(call_id)
        if call is None:
            raise UnknownCallIdError(f"Unknown call '{call_id}'")
        if call.status in ("completed", "failed", "cancelled", "committed"):
            return {"call_id": call_id, "cancelled": False,
                    "committed": call.committed, "status": call.status}
        manifest = self._manifests.get(call.tool_name, {})
        if not manifest.get("cancellable", True):
            return {"call_id": call_id, "cancelled": False,
                    "committed": call.committed, "status": call.status}
        ev = self._cancel_events.get(call_id)
        if ev is not None:
            ev.set()
        try:
            await self._provider.cancel(call_id)
        except Exception:
            pass
        # Status resolves when execute() observes the event; mark cancelled
        # optimistically only for non-commit-boundary tools.
        if not manifest.get("commit_boundary", False):
            call.status = "cancelled"
            call.updated_ts = time.time()
        return {"call_id": call_id, "cancelled": True,
                "committed": call.committed, "status": call.status}

    def get_call(self, call_id: str) -> ToolCall:
        # Defensive copy: ToolCall is mutable and the stored record must
        # never alias a caller-visible handle (same policy as StateManager).
        try:
            return copy.deepcopy(self._calls[call_id])
        except KeyError:
            raise UnknownCallIdError(f"Unknown call '{call_id}'") from None

    def call_status(self, call_id: str) -> str:
        return self.get_call(call_id).status
