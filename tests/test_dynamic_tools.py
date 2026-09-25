"""Dynamic tool support contract (Step 9 audit pins).

- Registry: schema-driven; registration/validation/lookup work for unseen
  tools; NO behavior is inferred from tool names (all semantics come from
  the manifest + provider contract).
- MockProvider: fixed tool set is an intentional harness limitation; an
  unseen tool fails with a structured, truthful envelope (never a crash).
- Full unseen-tool execution (commit/idempotency/cancel semantics) works
  through any ProviderAdapter implementation -- pinned via a minimal
  inline custom provider, WITHOUT expanding MockProvider.
"""

import asyncio

import pytest

from src.tools import InvalidArgumentsError, UnknownToolError, make_mock_registry
from src.tools.providers.base import ProviderAdapter, ProviderResult
from src.tools.registry import InvalidManifestError


def run(coro):
    return asyncio.run(coro)


HOTEL = {
    "name": "hotel_search",
    "kind": "read_only",
    "version": "1.0",
    "args_schema": {"city": "string", "checkin": "string"},
    "required_args": ["city"],
    "timeout_s": 20,
    "cancellable": True,
    "reversibility": "fully_interruptible",
    "commit_boundary": False,
    "idempotency": None,
}


class CustomProvider(ProviderAdapter):
    """Minimal unseen-tool implementation behind the provider contract."""
    name = "custom"

    def __init__(self):
        self.runs = 0

    async def execute(self, *, tool_name, args, call_id, cancel_event):
        self.runs += 1
        if cancel_event.is_set():
            return ProviderResult(ok=False, error="cancelled",
                                  committed=False)
        if tool_name == "refund_issue":
            return ProviderResult(ok=True, payload={"refund_id": "rf-1"},
                                  committed=True)
        return ProviderResult(ok=True, payload={"echo": dict(args)})

    async def cancel(self, call_id):
        return True


REFUND = {
    "name": "refund_issue",
    "kind": "compensating",
    "version": "3.2",
    "args_schema": {"booking_id": "string", "idempotency_key": "string"},
    "required_args": ["booking_id", "idempotency_key"],
    "timeout_s": 30,
    "cancellable": False,
    "reversibility": "compensating",
    "commit_boundary": True,
    "idempotency": {"key_field": "idempotency_key", "scope": "session"},
}


def test_a_b_register_and_lookup_unseen_tool():
    reg = make_mock_registry()
    with pytest.raises(UnknownToolError):
        reg.lookup("hotel_search")
    reg.register_tool(dict(HOTEL))
    m = reg.lookup("hotel_search")
    assert m["kind"] == "read_only" and m["version"] == "1.0"
    m["timeout_s"] = 999  # lookups are isolated copies
    assert reg.lookup("hotel_search")["timeout_s"] == 20
    assert "hotel_search" in reg.list_tools()


def test_c_argument_validation_for_unseen_tool():
    reg = make_mock_registry()
    reg.register_tool(dict(HOTEL))
    with pytest.raises(InvalidArgumentsError):
        run(reg.execute(tool_name="hotel_search", args={"bogus": 1},
                        call_id="h0", session_id="s", state_version=1))
    with pytest.raises(InvalidArgumentsError):  # missing required 'city'
        run(reg.execute(tool_name="hotel_search", args={"checkin": "x"},
                        call_id="h0", session_id="s", state_version=1))


def test_d_h_provider_missing_fails_structured_and_truthful():
    """Unseen manifest + MockProvider (no implementation): structured
    failed envelope, lifecycle recorded, nothing cached, no crash."""
    reg = make_mock_registry()
    reg.register_tool(dict(HOTEL))
    r = run(reg.execute(tool_name="hotel_search", args={"city": "Goa"},
                        call_id="h1", session_id="s", state_version=1))
    assert r["status"] == "failed"
    assert "hotel_search" in r["error"]
    assert r["committed"] is False
    assert r["idempotent_replay"] is False
    assert reg.call_status("h1") == "failed"
    r2 = run(reg.execute(tool_name="hotel_search", args={"city": "Goa"},
                         call_id="h2", session_id="s", state_version=1))
    assert r2["status"] == "failed" and r2["idempotent_replay"] is False


def test_e_invalid_manifests_rejected():
    reg = make_mock_registry()
    with pytest.raises(InvalidManifestError):
        reg.register_tool({"name": "x"})  # missing keys
    bad_kind = dict(HOTEL, name="bad_kind", kind="magic")
    with pytest.raises(InvalidManifestError):
        reg.register_tool(bad_kind)
    bad_commit = dict(HOTEL, name="bad_commit", commit_boundary=True)
    with pytest.raises(InvalidManifestError):  # commit only for commit kinds
        reg.register_tool(bad_commit)
    no_idem = {"name": "pay", "kind": "state_modifying", "version": "1",
               "args_schema": {}, "timeout_s": 5, "cancellable": True,
               "reversibility": "x"}
    with pytest.raises(InvalidManifestError):  # state-changing needs policy
        reg.register_tool(no_idem)
    assert "pay" not in reg.list_tools()  # rejected => not registered


def test_f_duplicate_name_overwrites_last_wins():
    reg = make_mock_registry()
    reg.register_tool(dict(HOTEL))
    v2 = dict(HOTEL)
    v2["version"] = "2.0"
    reg.register_tool(v2)  # same name: replace, no version-conflict error
    assert reg.lookup("hotel_search")["version"] == "2.0"


def test_g_manifest_drives_semantics_for_unseen_tool():
    """Commit boundary, idempotency, and non-cancellability are honored for
    a never-before-seen tool purely from its manifest -- proving no
    name-based inference anywhere in the Registry."""
    reg = make_mock_registry()
    reg.register_tool(dict(REFUND))
    reg.set_provider(CustomProvider())
    args = {"booking_id": "b1", "idempotency_key": "k9"}
    r1 = run(reg.execute(tool_name="refund_issue", args=dict(args),
                         call_id="g1", session_id="s", state_version=4))
    assert r1["status"] == "committed" and r1["committed"] is True
    r2 = run(reg.execute(tool_name="refund_issue", args=dict(args),
                         call_id="g2", session_id="s", state_version=4))
    assert r2["idempotent_replay"] is True
    assert r2["payload"] == r1["payload"]
    assert reg._provider.runs == 1
    out = run(reg.cancel_call("g1"))
    assert out == {"call_id": "g1", "cancelled": False,
                   "committed": True, "status": "committed"}
