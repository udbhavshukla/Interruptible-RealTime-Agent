"""Final integration-readiness pins (Step 13).

- Registry get_call() returns an isolated copy (mutable ToolCall records
  must never alias caller handles).
- Module-level manifest seed is never mutated by registration.
- Owner instances share nothing; no cross-owner imports exist
  (tools/protocol never touch state; multimodal never imports state).
"""

import asyncio

import src.tools.registry as registry_module
import src.multimodal.grounding as grounding_module
from src.state import StateManager
from src.tools import make_mock_registry
from src.tools.manifests.definitions import TOOL_MANIFESTS


def run(coro):
    return asyncio.run(coro)


def test_registry_get_call_isolated():
    reg = make_mock_registry()
    run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                    call_id="c1", session_id="s", state_version=1))
    handle = reg.get_call("c1")
    handle.status = "FORGED"
    handle.args["query"] = "FORGED"
    fresh = reg.get_call("c1")
    assert fresh.status == "completed"
    assert fresh.args == {"query": "x"}
    assert reg.call_status("c1") == "completed"


def test_manifest_seed_never_mutated():
    before = {k: v["version"] for k, v in TOOL_MANIFESTS.items()}
    n = len(TOOL_MANIFESTS)
    reg = make_mock_registry()
    reg.register_tool({"name": "hotel_search", "kind": "read_only",
                       "version": "1.0", "args_schema": {"city": "string"},
                       "timeout_s": 20, "cancellable": True,
                       "reversibility": "fully_interruptible"})
    m = reg.lookup("flight_search")
    m["timeout_s"] = 999
    assert len(TOOL_MANIFESTS) == n
    assert {k: v["version"] for k, v in TOOL_MANIFESTS.items()} == before
    assert make_mock_registry().lookup("flight_search")["timeout_s"] == 20


def test_owner_instances_share_nothing():
    sm1, sm2 = StateManager(), StateManager()
    sm1.create_session("s")
    sm1.update_intent("s", "flight_search")
    try:
        sm2.get_state("s")
        raised = False
    except Exception:
        raised = True
    assert raised  # sessions do not leak across manager instances
    r1, r2 = make_mock_registry(), make_mock_registry()
    run(r1.execute(tool_name="manual_lookup", args={"query": "x"},
                   call_id="c1", session_id="s", state_version=1))
    try:
        r2.get_call("c1")
        raised = False
    except Exception:
        raised = True
    assert raised  # call tables do not leak across registries


def test_no_cross_owner_imports():
    # Tools/protocol must never reach into state; multimodal takes the
    # manager duck-typed (no import) so no ownership bypass is possible
    # at module level.
    assert not hasattr(registry_module, "StateManager")
    assert not hasattr(registry_module, "update_slots")
    assert not hasattr(grounding_module, "StateManager")
    import src.protocol.schemas as proto

    assert not hasattr(proto, "StateManager")
    assert not hasattr(proto, "ToolRegistry")
