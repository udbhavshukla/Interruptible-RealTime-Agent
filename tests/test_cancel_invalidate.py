"""R1 tests: registry cancellation vs state invalidation consistency.

Ownership rule under test:
- ToolRegistry owns execution/lifecycle/cancellation.
- StateManager owns binding/version/invalidation/stale determination.
- The Coordinator must perform BOTH steps; registry cancellation alone
  does NOT invalidate the state binding.
"""

import asyncio

import pytest

from src.state import StateManager, UnknownCallError
from src.tools import make_mock_registry


def run(coro):
    return asyncio.run(coro)


ARGS = {"origin": "Bangalore", "destination": "Delhi", "date": "2026-09-25"}


def _flight_session(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    sm.update_slots(sid, dict(ARGS))
    return sm.current_version(sid)


def test_a_registry_cancel_plus_invalidation_rejects_late_result():
    sm = StateManager()
    reg = make_mock_registry()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v,
                     tool_name="flight_search")
    reg._provider.set_latency("flight_search", 5.0)

    async def scenario():
        task = asyncio.create_task(reg.execute(
            tool_name="flight_search", args=dict(ARGS),
            call_id="call_001", session_id="s_42", state_version=v))
        await asyncio.sleep(0.05)
        out = await reg.cancel_call("call_001")
        # Coordinator second step: invalidate the state binding.
        invalidated = sm.invalidate_call("s_42", "call_001")
        res = await task
        return out, invalidated, res

    out, invalidated, res = run(scenario())
    assert out["cancelled"] is True
    assert invalidated is True
    assert res["status"] == "cancelled"
    # Late result at the same version is now stale: binding is gone.
    assert sm.is_stale("s_42", v, call_id="call_001") is True


def test_b_successful_completion_result_accepted():
    sm = StateManager()
    reg = make_mock_registry()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v,
                     tool_name="flight_search")
    res = run(reg.execute(tool_name="flight_search", args=dict(ARGS),
                          call_id="call_001", session_id="s_42",
                          state_version=v))
    assert res["status"] == "completed"
    # Success path never invalidates: binding intact, result accepted.
    assert sm.get_call("s_42", "call_001").call_id == "call_001"
    assert sm.is_stale("s_42", v, call_id="call_001") is False


def test_c_registry_cancel_alone_does_not_invalidate_state():
    """Documents the architectural requirement: cancel_call() only affects
    the registry. Until the Coordinator also invalidates the binding,
    is_stale() still accepts a current-version result for the call."""
    sm = StateManager()
    reg = make_mock_registry()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v,
                     tool_name="flight_search")
    reg._provider.set_latency("flight_search", 5.0)

    async def scenario():
        task = asyncio.create_task(reg.execute(
            tool_name="flight_search", args=dict(ARGS),
            call_id="call_001", session_id="s_42", state_version=v))
        await asyncio.sleep(0.05)
        out = await reg.cancel_call("call_001")
        # NOTE: deliberately NO sm.invalidate_call here.
        still_fresh = sm.is_stale("s_42", v, call_id="call_001")
        res = await task
        return out, still_fresh, res

    out, still_fresh, res = run(scenario())
    assert out["cancelled"] is True
    assert res["status"] == "cancelled"
    assert still_fresh is False  # binding untouched -> gate passes
    # ...which is exactly why the Coordinator must invalidate (see TEST A).


def test_d_invalidation_is_idempotent():
    sm = StateManager()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v)
    assert sm.invalidate_call("s_42", "call_001") is True
    assert sm.invalidate_call("s_42", "call_001") is False  # no raise
    assert sm.invalidate_call("s_42", "never_existed") is False
    # State itself uncorrupted: version and slots unchanged.
    assert sm.current_version("s_42") == v
    assert sm.get_state("s_42").slots["destination"] == "Delhi"
    # Strict legacy wrapper keeps its contract.
    with pytest.raises(UnknownCallError):
        sm.remove_call("s_42", "call_001")


def test_e_version_staleness_unchanged():
    sm = StateManager()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v)
    # Version leg is independent of the call leg: binding present, yet
    # an older version is stale and the current version is fresh.
    assert sm.is_stale("s_42", v - 1, call_id="call_001") is True
    assert sm.is_stale("s_42", v, call_id="call_001") is False
    assert sm.is_stale("s_42", v - 1) is True
