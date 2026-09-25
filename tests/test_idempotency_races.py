"""Idempotency + commit-boundary contract pins (Step 7 audit).

Identity split under test:
- call_id        = execution identity (unique per attempt, never reused)
- idempotency_key = effect/replay identity, scoped as session|tool|key
"""

import asyncio

import pytest

from src.tools import (
    DuplicateCallIdError,
    InvalidArgumentsError,
    make_mock_registry,
)


def run(coro):
    return asyncio.run(coro)


ARGS = {"option_id": "opt-7", "passenger": "Asha",
        "idempotency_key": "session123-booking-option7"}


def test_concurrent_duplicates_execute_effect_once():
    """Duplicate arriving WHILE the first is running must replay, not
    double-execute (per-key serialization; regression: commit_count was 2)."""
    async def scenario():
        reg = make_mock_registry()
        reg._provider.set_latency("booking", 0.3)
        r1, r2 = await asyncio.gather(
            reg.execute(tool_name="booking", args=dict(ARGS),
                        call_id="c1", session_id="s", state_version=1),
            reg.execute(tool_name="booking", args=dict(ARGS),
                        call_id="c2", session_id="s", state_version=1))
        return reg, r1, r2

    reg, r1, r2 = run(scenario())
    assert reg._provider.commit_count == 1
    assert sorted([r1["idempotent_replay"],
                   r2["idempotent_replay"]]) == [False, True]
    assert r1["payload"] == r2["payload"]
    assert r1["status"] == r2["status"] == "committed"


def test_unrelated_calls_still_run_concurrently():
    """Per-key locks must not serialize independent executions."""
    async def scenario():
        reg = make_mock_registry()
        r1, r2 = await asyncio.gather(
            reg.execute(tool_name="flight_search",
                        args={"origin": "A", "destination": "B",
                              "date": "D"},
                        call_id="c1", session_id="s", state_version=1),
            reg.execute(tool_name="manual_lookup", args={"query": "x"},
                        call_id="c2", session_id="s", state_version=1))
        return r1, r2

    r1, r2 = run(scenario())
    assert r1["status"] == r2["status"] == "completed"
    assert r1["idempotent_replay"] is r2["idempotent_replay"] is False


def test_same_key_different_args_returns_original():
    """Same key = same operation. A repeat with altered args replays the
    ORIGINAL effect result instead of executing a second effect."""
    reg = make_mock_registry()
    r1 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c1", session_id="s", state_version=1))
    altered = dict(ARGS)
    altered["passenger"] = "Babu"
    r2 = run(reg.execute(tool_name="booking", args=altered,
                         call_id="c2", session_id="s", state_version=1))
    assert reg._provider.commit_count == 1
    assert r2["idempotent_replay"] is True
    assert r2["payload"] == r1["payload"]
    assert r2["payload"]["passenger"] == "Asha"  # original wins


def test_replay_preserves_original_version_stamp():
    """Replay envelopes carry the ORIGINAL execution's state_version, not
    the replay request's. M2 must special-case idempotent_replay instead
    of applying the version gate (latest-intent-wins would otherwise
    launder an older-intent effect as current)."""
    reg = make_mock_registry()
    run(reg.execute(tool_name="booking", args=dict(ARGS),
                    call_id="c1", session_id="s", state_version=1))
    r2 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c2", session_id="s", state_version=2))
    assert r2["idempotent_replay"] is True
    assert r2["state_version"] == 1
    assert r2["committed"] is True


def test_same_call_id_replay_raises():
    """call_id is execution identity: reusing one -- even for an
    idempotent replay -- raises instead of replaying."""
    reg = make_mock_registry()
    run(reg.execute(tool_name="booking", args=dict(ARGS),
                    call_id="c1", session_id="s", state_version=1))
    with pytest.raises(DuplicateCallIdError):
        run(reg.execute(tool_name="booking", args=dict(ARGS),
                        call_id="c1", session_id="s", state_version=1))


def test_invalid_args_rejected_despite_cached_key():
    """Argument validation runs before the idempotency short-circuit."""
    reg = make_mock_registry()
    run(reg.execute(tool_name="booking", args=dict(ARGS),
                    call_id="c1", session_id="s", state_version=1))
    bad = {"option_id": "opt-7",
           "idempotency_key": "session123-booking-option7"}  # no passenger
    with pytest.raises(InvalidArgumentsError):
        run(reg.execute(tool_name="booking", args=bad,
                        call_id="c2", session_id="s", state_version=1))


def test_same_key_different_tools_are_independent():
    """Scope is session|tool|key: the same key under another tool is a
    different effect identity."""
    reg = make_mock_registry()
    r1 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c1", session_id="s", state_version=1))
    r2 = run(reg.execute(
        tool_name="cancel_booking",
        args={"booking_id": "b1",
              "idempotency_key": "session123-booking-option7"},
        call_id="c2", session_id="s", state_version=1))
    assert r1["idempotent_replay"] is False
    assert r2["idempotent_replay"] is False
    assert r2["status"] == "committed"


def test_cancel_before_commit_prevents_effect():
    """Cooperative cancel landing BEFORE the boundary: no effect, and the
    cancelled attempt is NOT cached (a retry executes fresh)."""
    async def scenario():
        reg = make_mock_registry()
        reg._provider.set_latency("booking", 5.0)
        task = asyncio.create_task(reg.execute(
            tool_name="booking", args=dict(ARGS),
            call_id="c1", session_id="s", state_version=1))
        await asyncio.sleep(0.05)
        out = await reg.cancel_call("c1")
        res = await task
        return reg, out, res

    reg, out, res = run(scenario())
    assert out["cancelled"] is True
    assert res["status"] == "cancelled"
    assert res["committed"] is False
    assert reg._provider.commit_count == 0
    # Retry with the same key executes (nothing was cached).
    reg._provider.set_latency("booking", 0.0)
    r2 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c2", session_id="s", state_version=1))
    assert r2["status"] == "committed"
    assert r2["idempotent_replay"] is False


def test_timeout_then_retry_executes_once():
    reg = make_mock_registry()
    reg._provider.set_latency("booking", 5.0)
    r1 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c1", session_id="s", state_version=1,
                         timeout_s=0.05))
    assert r1["status"] == "failed" and r1["error"] == "timeout"
    reg._provider.set_latency("booking", 0.0)
    r2 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c2", session_id="s", state_version=1))
    assert r2["status"] == "committed"
    assert r2["idempotent_replay"] is False
    assert reg._provider.commit_count == 1


def test_failure_then_retry_executes_fresh():
    """Failed attempts are never cached: the retry is a real execution."""
    reg = make_mock_registry()
    reg._provider.inject_failure("booking", "gateway_down")
    r1 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c1", session_id="s", state_version=1))
    assert r1["status"] == "failed"
    reg._provider.clear_failure("booking")
    r2 = run(reg.execute(tool_name="booking", args=dict(ARGS),
                         call_id="c2", session_id="s", state_version=1))
    assert r2["status"] == "committed"
    assert r2["idempotent_replay"] is False
    assert reg._provider.commit_count == 1
