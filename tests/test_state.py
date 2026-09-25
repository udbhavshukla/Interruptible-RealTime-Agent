"""State Manager tests: all invariants from the Member 3 spec."""

import pytest

from src.state import (
    DuplicateCallError,
    InvalidSlotError,
    StateManager,
    UnknownSessionError,
)


@pytest.fixture
def sm():
    return StateManager()


def _flight_session(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    return sm.update_slots(sid, {"origin": "Bangalore",
                                 "destination": "Delhi",
                                 "date": "2026-09-25"})


def test_create_session(sm):
    snap = sm.create_session("s_1")
    assert snap.session_id == "s_1"
    assert snap.state_version == 0


def test_new_session_state_empty(sm):
    sm.create_session("s_1")
    snap = sm.get_state("s_1")
    assert snap.intent == "none"
    assert snap.slots == {}
    assert snap.active_calls == []


def test_unknown_session_raises(sm):
    with pytest.raises(UnknownSessionError):
        sm.get_state("nope")


def test_update_intent(sm):
    sm.create_session("s_1")
    snap = sm.update_intent("s_1", "flight_search")
    assert snap.intent == "flight_search"


def test_update_one_slot(sm):
    sm.create_session("s_1")
    sm.update_intent("s_1", "flight_search")
    snap = sm.update_slot("s_1", "origin", "Bangalore")
    assert snap.slots["origin"] == "Bangalore"


def test_update_multiple_slots(sm):
    sm.create_session("s_1")
    sm.update_intent("s_1", "flight_search")
    snap = sm.update_slots("s_1", {"origin": "Bangalore", "date": "2026-09-25"})
    assert snap.slots["origin"] == "Bangalore"
    assert snap.slots["date"] == "2026-09-25"


def test_preserve_unrelated_slots(sm):
    snap = _flight_session(sm)
    assert snap.slots == {"origin": "Bangalore", "destination": "Delhi",
                          "date": "2026-09-25"}


def test_localized_correction(sm):
    _flight_session(sm)
    snap = sm.update_slot("s_42", "destination", "Mumbai")
    # Only destination changes; origin/date preserved; version bumped by 1.
    assert snap.slots == {"origin": "Bangalore", "destination": "Mumbai",
                          "date": "2026-09-25"}


def test_state_version_increments_monotonically(sm):
    sm.create_session("s_1")
    v0 = sm.current_version("s_1")
    sm.update_intent("s_1", "flight_search")
    v1 = sm.current_version("s_1")
    sm.update_slot("s_1", "origin", "Bangalore")
    v2 = sm.current_version("s_1")
    assert v0 < v1 < v2
    # No-op writes do not bump the version.
    sm.update_slot("s_1", "origin", "Bangalore")
    assert sm.current_version("s_1") == v2


def test_snapshots_are_immutable(sm):
    snap = _flight_session(sm)
    snap.slots["destination"] = "HACKED"
    snap.active_calls.append("fake")
    fresh = sm.get_state("s_42")
    assert fresh.slots["destination"] == "Delhi"
    assert "fake" not in fresh.active_calls


def test_history_contains_previous_versions(sm):
    _flight_session(sm)
    sm.update_slot("s_42", "destination", "Mumbai")
    hist = sm.get_history("s_42")
    versions = [s.state_version for s in hist]
    assert versions == sorted(versions)
    assert len(hist) >= 3
    assert hist[-2].slots["destination"] == "Delhi"  # old snapshot intact
    assert hist[-1].slots["destination"] == "Mumbai"


def test_sessions_are_isolated(sm):
    sm.create_session("a")
    sm.create_session("b")
    sm.update_intent("a", "flight_search")
    sm.update_intent("b", "flight_search")
    sm.update_slot("a", "destination", "Delhi")
    sm.update_slot("b", "destination", "Mumbai")
    assert sm.get_state("a").slots["destination"] == "Delhi"
    assert sm.get_state("b").slots["destination"] == "Mumbai"


def test_unknown_slot_rejected(sm):
    sm.create_session("s_1")
    sm.update_intent("s_1", "flight_search")
    with pytest.raises(InvalidSlotError):
        sm.update_slot("s_1", "passenger_name", "Bob")
    # State untouched by the rejected write.
    assert sm.get_state("s_1").slots == {}


def test_stale_version_detection(sm):
    _flight_session(sm)  # v3
    v = sm.current_version("s_42")
    sm.register_call("s_42", "call_001", state_version=v - 1,
                     tool_name="flight_search")
    assert sm.is_stale("s_42", v - 1) is True
    assert sm.is_stale("s_42", v) is False
    # Unknown/inactive call ids are stale.
    assert sm.is_stale("s_42", v, call_id="ghost") is True
    assert sm.is_stale("s_42", v, call_id="call_001") is False
    # Cancelled calls go stale.
    sm.remove_call("s_42", "call_001")
    assert sm.is_stale("s_42", v, call_id="call_001") is True


def test_duplicate_call_id_rejected(sm):
    _flight_session(sm)
    v = sm.current_version("s_42")
    sm.register_call("s_42", "call_001", state_version=v)
    with pytest.raises(DuplicateCallError):
        sm.register_call("s_42", "call_001", state_version=v)


def test_canonical_interruption(sm):
    """T0-T9 core invariant: stale Delhi result must never overwrite Mumbai."""
    sm.create_session("s_42")
    sm.update_intent("s_42", "flight_search")
    sm.update_slots("s_42", {"origin": "Bangalore", "destination": "Delhi",
                             "date": "2026-09-25"})
    v1 = sm.current_version("s_42")
    sm.register_call("s_42", "call_001", state_version=v1,
                     tool_name="flight_search")

    # Interruption: localized correction.
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    assert v2 == v1 + 1
    sm.register_call("s_42", "call_002", state_version=v2,
                     tool_name="flight_search")

    # Old result arrives late -> stale, must be rejected.
    assert sm.is_stale("s_42", v1, call_id="call_001") is True
    # New result -> current, may be accepted.
    assert sm.is_stale("s_42", v2, call_id="call_002") is False
    assert sm.get_state("s_42").slots["destination"] == "Mumbai"
