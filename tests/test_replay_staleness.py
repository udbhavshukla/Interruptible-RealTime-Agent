"""Replay-vs-staleness mechanics (Step 8 audit pins).

Member-3-owned invariants only -- no Coordinator policy implemented here:
- Case A: effect cached at v1, session advances, same key re-requested at
  v2 -> replay envelope keeps the ORIGINAL version (v1) with
  idempotent_replay=True; the version gate sees it as old.
- Case B: attempt interrupted pre-commit (nothing cached), retry at v2 ->
  FRESH execution at v2, replay=False.
- Read-only tools have no idempotency policy and therefore never replay.
- A replay mints a NEW ToolCall record stamped with the REQUEST version
  while the envelope preserves the EFFECT version (two referents, both
  truthful; see Step 7 report).
"""

import asyncio

from src.state import StateManager
from src.tools import make_mock_registry


def run(coro):
    return asyncio.run(coro)


ARGS = {"option_id": "opt-7", "destination": "Delhi"}
BOOK = {"option_id": "opt-7", "passenger": "Asha",
        "idempotency_key": "sess-booking-opt7"}


def _flight_session(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    sm.update_slots(sid, {"origin": "Bangalore", "destination": "Delhi",
                          "date": "2026-09-25"})
    return sm.current_version(sid)


def test_case_a_replay_after_advance_keeps_original_version():
    sm = StateManager()
    reg = make_mock_registry()
    v1 = _flight_session(sm)
    sm.register_call("s_42", "call_A", state_version=v1, tool_name="booking")
    first = run(reg.execute(tool_name="booking", args=dict(BOOK),
                            call_id="call_A", session_id="s_42",
                            state_version=v1))
    assert first["status"] == "committed" and not first["idempotent_replay"]

    # Session advances on a newer intent; old binding invalidated.
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    sm.invalidate_call("s_42", "call_A")

    # Same effect identity requested again at v2 -> replay, effect intact.
    sm.register_call("s_42", "call_B", state_version=v2, tool_name="booking")
    replay = run(reg.execute(tool_name="booking", args=dict(BOOK),
                             call_id="call_B", session_id="s_42",
                             state_version=v2))
    assert replay["idempotent_replay"] is True
    assert replay["committed"] is True
    assert replay["payload"] == first["payload"]
    assert replay["state_version"] == v1  # original, never rewritten
    # ...so the normal version gate (Member-3 predicate) sees it as old:
    assert sm.is_stale("s_42", replay["state_version"],
                       call_id="call_B") is True


def test_case_b_retry_after_precommit_cancel_is_fresh_at_new_version():
    sm = StateManager()
    reg = make_mock_registry()
    v1 = _flight_session(sm)
    sm.register_call("s_42", "call_A", state_version=v1, tool_name="booking")
    reg._provider.set_latency("booking", 5.0)

    async def interrupt():
        task = asyncio.create_task(reg.execute(
            tool_name="booking", args=dict(BOOK),
            call_id="call_A", session_id="s_42", state_version=v1))
        await asyncio.sleep(0.05)
        out = await reg.cancel_call("call_A")
        sm.invalidate_call("s_42", "call_A")
        return out, await task

    out, res = run(interrupt())
    assert res["status"] == "cancelled" and not res["committed"]

    # Retry at v2: nothing was cached, so this is a genuine fresh effect.
    reg._provider.set_latency("booking", 0.0)
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    sm.register_call("s_42", "call_B", state_version=v2, tool_name="booking")
    retry = run(reg.execute(tool_name="booking", args=dict(BOOK),
                            call_id="call_B", session_id="s_42",
                            state_version=v2))
    assert retry["idempotent_replay"] is False
    assert retry["status"] == "committed"
    assert retry["state_version"] == v2
    assert sm.is_stale("s_42", v2, call_id="call_B") is False
    assert reg._provider.commit_count == 1  # exactly one real effect


def test_read_only_results_never_replay():
    reg = make_mock_registry()
    args = {"origin": "A", "destination": "B", "date": "D"}
    r1 = run(reg.execute(tool_name="flight_search", args=dict(args),
                         call_id="c1", session_id="s", state_version=1))
    r2 = run(reg.execute(tool_name="flight_search", args=dict(args),
                         call_id="c2", session_id="s", state_version=1))
    assert r1["idempotent_replay"] is False
    assert r2["idempotent_replay"] is False
    assert r1["payload"] == r2["payload"]  # deterministic, not cached


def test_replay_record_carries_request_version_envelope_carries_effect():
    reg = make_mock_registry()
    run(reg.execute(tool_name="booking", args=dict(BOOK),
                    call_id="c1", session_id="s", state_version=1))
    replay = run(reg.execute(tool_name="booking", args=dict(BOOK),
                             call_id="c2", session_id="s", state_version=2))
    record = reg.get_call("c2")
    assert record.call_id == "c2"
    assert record.state_version == 2  # this execution attempt's context
    assert replay["call_id"] == "c2"
    assert replay["state_version"] == 1  # the effect's original context
    assert replay["idempotent_replay"] is True
