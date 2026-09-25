"""StateManager versioning/snapshot/history/isolation semantics (Step 11).

- Exactly one +1 per real change; +0 for no-ops; +0 and no partial
  mutation on failed validation.
- Snapshots/history entries/bindings are defensive copies: mutating any
  returned handle never corrupts live state or the audit trail.
- Sessions fully isolated; no global mutable state; repeat create resets.
"""

import pytest

from src.state import (
    DuplicateCallError,
    InvalidSlotError,
    StateManager,
    UnknownCallError,
    UnknownSessionError,
)


@pytest.fixture
def sm():
    return StateManager()


def test_first_mutation_bumps_exactly_once(sm):
    sm.create_session("s")
    assert sm.current_version("s") == 0
    sm.update_intent("s", "flight_search")
    assert sm.current_version("s") == 1


def test_batch_multi_slot_single_bump(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    n_hist = len(sm.get_history("s"))
    sm.update_slots("s", {"origin": "A", "destination": "B",
                          "date": "D"})
    assert sm.current_version("s") == 2  # one atomic batch -> +1
    assert len(sm.get_history("s")) == n_hist + 1


def test_failed_validation_no_bump_no_history_no_partial(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    sm.update_slot("s", "origin", "A")
    v, n_hist = sm.current_version("s"), len(sm.get_history("s"))
    with pytest.raises(InvalidSlotError):
        sm.update_slots("s", {"destination": "B", "bogus": 1})
    assert sm.current_version("s") == v
    assert len(sm.get_history("s")) == n_hist
    assert sm.get_state("s").slots == {"origin": "A"}  # nothing applied


def test_noop_writes_no_bump_no_history(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    sm.update_slot("s", "origin", "A")
    v, n_hist = sm.current_version("s"), len(sm.get_history("s"))
    sm.update_slot("s", "origin", "A")  # identical
    sm.update_slots("s", {})  # empty
    sm.update_intent("s", "flight_search")  # same intent
    assert sm.current_version("s") == v
    assert len(sm.get_history("s")) == n_hist


def test_history_versions_monotonic_and_unique(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    sm.update_slot("s", "origin", "A")
    sm.update_slot("s", "destination", "B")
    versions = [h.state_version for h in sm.get_history("s")]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)  # each commit exactly once
    assert versions[-1] == sm.current_version("s")


def test_history_entries_independent_of_callers(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    sm.update_slot("s", "origin", "A")
    hist = sm.get_history("s")
    hist[0].slots["INJECTED"] = True  # mutate a returned handle...
    hist.append(hist[0])
    fresh = sm.get_history("s")  # ...stored trail must be unaffected.
    assert all("INJECTED" not in h.slots for h in fresh)
    assert len(fresh) == 3  # v0, intent, slot


def test_returned_snapshots_do_not_alias_history(sm):
    created = sm.create_session("s")  # returned object...
    created.slots["INJECTED"] = True  # ...mutated by caller...
    assert all("INJECTED" not in h.slots for h in sm.get_history("s"))
    committed = sm.update_intent("s", "flight_search")
    committed.slots["INJECTED"] = True
    assert all("INJECTED" not in h.slots for h in sm.get_history("s"))
    assert sm.get_state("s").slots == {}


def test_binding_args_isolated_from_callers(sm):
    sm.create_session("s")
    returned = sm.register_call("s", "c1", 0, tool_name="t",
                                args={"nested": {"x": 1}})
    returned.args["INJECTED"] = True
    returned.args["nested"]["x"] = 999
    stored = sm.get_call("s", "c1")
    assert stored.args == {"nested": {"x": 1}}
    stored.args["OTHER"] = True  # mutating a read handle is also safe
    assert sm.get_call("s", "c1").args == {"nested": {"x": 1}}


def test_sessions_fully_independent(sm):
    sm.create_session("a")
    sm.create_session("b")
    sm.update_intent("a", "flight_search")
    assert sm.current_version("a") == 1
    assert sm.current_version("b") == 0  # untouched
    sm.register_call("a", "c1", 1)
    sm.register_call("b", "c1", 0)  # same call_id legal across sessions
    assert sm.invalidate_call("a", "c1") is True
    assert sm.get_call("b", "c1").call_id == "c1"  # b unaffected
    assert len(sm.get_history("a")) == 2
    assert len(sm.get_history("b")) == 1


def test_recreate_resets_version_and_history(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    sm.update_slot("s", "origin", "A")
    sm.create_session("s")  # reset by design
    assert sm.current_version("s") == 0
    assert len(sm.get_history("s")) == 1
    assert sm.get_state("s").slots == {}
    assert sm.get_state("s").active_calls == []


def test_edge_cases_rejected_structured(sm):
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    with pytest.raises(InvalidSlotError):
        sm.update_slot("s", "", "x")  # empty slot name
    with pytest.raises(ValueError):
        sm.create_session("")  # empty session id
    for op in (sm.get_state, sm.get_snapshot, sm.get_history,
               sm.current_version, sm.validate_state):
        with pytest.raises(UnknownSessionError):
            op("ghost")
    with pytest.raises(UnknownSessionError):
        sm.update_slot("ghost", "origin", "A")
    with pytest.raises(UnknownSessionError):
        sm.register_call("ghost", "c", 0)
    with pytest.raises(UnknownSessionError):
        sm.invalidate_call("ghost", "c")
    with pytest.raises(UnknownSessionError):
        sm.is_stale("ghost", 0)
    with pytest.raises(UnknownSessionError):
        sm.remove_call("ghost", "c")
    with pytest.raises(UnknownCallError):  # strict wrapper, known session
        sm.remove_call("s", "ghost")
    with pytest.raises(DuplicateCallError):
        sm.register_call("s", "c1", 0)
        sm.register_call("s", "c1", 0)
