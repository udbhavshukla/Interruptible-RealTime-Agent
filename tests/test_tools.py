"""Tool Registry tests: manifests, lifecycle, idempotency, commit boundary."""

import asyncio

import pytest

from src.tools import (
    DuplicateCallIdError,
    InvalidArgumentsError,
    InvalidManifestError,
    ToolRegistry,
    UnknownCallIdError,
    UnknownToolError,
    make_mock_registry,
    validate_manifest,
)


@pytest.fixture
def reg():
    return make_mock_registry()


def run(coro):
    return asyncio.run(coro)


def test_register_and_lookup(reg):
    m = reg.lookup("flight_search")
    assert m["name"] == "flight_search"
    assert m["kind"] == "read_only"


def test_lookup_unknown_raises(reg):
    with pytest.raises(UnknownToolError):
        reg.lookup("teleport")


def test_manifest_validation_rejects_bad():
    with pytest.raises(InvalidManifestError):
        validate_manifest({"name": "x"})  # missing keys
    with pytest.raises(InvalidManifestError):
        validate_manifest({"name": "x", "kind": "magic", "version": "1",
                           "args_schema": {}, "timeout_s": 1,
                           "cancellable": True, "reversibility": "none"})


def test_state_modifying_requires_idempotency_policy(reg):
    with pytest.raises(InvalidManifestError):
        reg.register_tool({"name": "pay", "kind": "state_modifying",
                           "version": "1", "args_schema": {}, "timeout_s": 5,
                           "cancellable": True, "reversibility": "x"})


def test_invalid_arguments_rejected(reg):
    with pytest.raises(InvalidArgumentsError):
        run(reg.execute(tool_name="flight_search",
                        args={"origin": "Bangalore"},  # missing fields
                        call_id="c1", session_id="s", state_version=1))
    with pytest.raises(InvalidArgumentsError):
        run(reg.execute(tool_name="flight_search",
                        args={"origin": "A", "destination": "B",
                              "date": "D", "bogus": 1},
                        call_id="c2", session_id="s", state_version=1))


def test_read_only_execution_deterministic(reg):
    args = {"origin": "Bangalore", "destination": "Delhi",
            "date": "2026-09-25"}
    r1 = run(reg.execute(tool_name="flight_search", args=args,
                         call_id="c1", session_id="s", state_version=1))
    r2 = run(reg.execute(tool_name="flight_search", args=args,
                         call_id="c2", session_id="s", state_version=1))
    assert r1["status"] == "completed"
    assert r1["payload"] == r2["payload"]  # deterministic
    assert r1["payload"]["destination"] == "Delhi"
    assert reg.call_status("c1") == "completed"


def test_reversible_tool_not_committed(reg):
    r = run(reg.execute(tool_name="reserve",
                        args={"option_id": "opt-1", "session_id": "s"},
                        call_id="c1", session_id="s", state_version=1))
    assert r["status"] == "completed"
    assert r["committed"] is False


def test_state_changing_tool_commits(reg):
    r = run(reg.execute(tool_name="booking",
                        args={"option_id": "opt-1", "passenger": "Asha",
                              "idempotency_key": "k1"},
                        call_id="c1", session_id="s", state_version=1))
    assert r["status"] == "committed"
    assert r["committed"] is True
    assert "booking_id" in r["payload"]


def test_call_lifecycle_recorded(reg):
    run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                    call_id="c1", session_id="s", state_version=3))
    call = reg.get_call("c1")
    assert call.call_id == "c1"
    assert call.state_version == 3
    assert call.status == "completed"


def test_duplicate_call_id_protection(reg):
    run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                    call_id="c1", session_id="s", state_version=1))
    with pytest.raises(DuplicateCallIdError):
        run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                        call_id="c1", session_id="s", state_version=1))


def test_cancellation_hook_read_only(reg):
    provider = reg._provider
    provider.set_latency("flight_search", 5.0)

    async def scenario():
        task = asyncio.create_task(reg.execute(
            tool_name="flight_search",
            args={"origin": "A", "destination": "B", "date": "D"},
            call_id="c1", session_id="s", state_version=1))
        await asyncio.sleep(0.05)
        out = await reg.cancel_call("c1")
        res = await task
        return out, res

    out, res = run(scenario())
    assert out["cancelled"] is True
    assert res["status"] == "cancelled"
    assert res["committed"] is False


def test_cancel_unknown_call_raises(reg):
    with pytest.raises(UnknownCallIdError):
        run(reg.cancel_call("ghost"))


def test_failure_injection(reg):
    reg._provider.inject_failure("manual_lookup", "boom")
    r = run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                        call_id="c1", session_id="s", state_version=1))
    assert r["status"] == "failed"
    assert r["error"] == "boom"


def test_timeout_handling(reg):
    reg._provider.set_latency("manual_lookup", 5.0)
    r = run(reg.execute(tool_name="manual_lookup", args={"query": "x"},
                        call_id="c1", session_id="s", state_version=1,
                        timeout_s=0.05))
    assert r["status"] == "failed"
    assert r["error"] == "timeout"


def test_commit_boundary_truthful_after_commit(reg):
    """Cancellation after commit must NOT be faked as success."""
    r = run(reg.execute(tool_name="booking",
                        args={"option_id": "o", "passenger": "P",
                              "idempotency_key": "k-commit"},
                        call_id="c1", session_id="s", state_version=1))
    assert r["committed"] is True
    out = run(reg.cancel_call("c1"))
    assert out["committed"] is True
    assert out["cancelled"] is False  # already terminal: no fake cancel
    assert reg.call_status("c1") == "committed"


def test_idempotency_duplicate_booking_executes_once(reg):
    args = {"option_id": "opt-7", "passenger": "Asha",
            "idempotency_key": "session123-booking-option7"}
    r1 = run(reg.execute(tool_name="booking", args=args,
                         call_id="call_001", session_id="s", state_version=1))
    count_after_first = reg._provider.commit_count
    r2 = run(reg.execute(tool_name="booking", args=args,
                         call_id="call_002", session_id="s", state_version=1))
    assert r1["status"] == "committed"
    assert r2["idempotent_replay"] is True
    assert r2["payload"] == r1["payload"]  # deterministic same result
    assert reg._provider.commit_count == count_after_first  # no 2nd effect


def test_idempotency_scoped_per_session(reg):
    args = {"option_id": "o", "passenger": "P", "idempotency_key": "same-key"}
    run(reg.execute(tool_name="booking", args=args, call_id="c1",
                    session_id="sA", state_version=1))
    n = reg._provider.commit_count
    r = run(reg.execute(tool_name="booking", args=args, call_id="c2",
                        session_id="sB", state_version=1))
    assert r["idempotent_replay"] is False  # different session -> new effect
    assert reg._provider.commit_count == n + 1
