"""R3 regression tests: stale version-bound proposals must never overwrite
newer state. (Step 3 fix: GroundedProposal.state_version gate.)"""

from src.multimodal import apply_verified, ground_facts
from src.state import StateManager


def _flight_v1(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    sm.update_slots(sid, {"origin": "Bangalore", "destination": "Delhi",
                          "date": "2026-09-25"})
    return sm.current_version(sid)


def test_a_stale_v1_proposal_skipped_after_move_to_v2():
    sm = StateManager()
    v1 = _flight_v1(sm)
    # Vision grounds Delhi at v1 (0.88 -> verified).
    proposals = ground_facts({"destination": "Delhi"}, confidence=0.88,
                             source="vision", state_version=v1)
    assert all(p.verified for p in proposals)

    # User corrects -> v2 (Mumbai).
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    assert v2 == v1 + 1

    snap, skipped = apply_verified(sm, "s_42", proposals)
    assert len(skipped) == 1
    assert skipped[0].reason.startswith("stale_version_")
    # State remains v2/Mumbai: old value did NOT overwrite.
    assert snap.slots["destination"] == "Mumbai"
    assert sm.current_version("s_42") == v2
    assert sm.get_state("s_42").slots["destination"] == "Mumbai"


def test_b_current_version_proposal_accepted():
    sm = StateManager()
    v1 = _flight_v1(sm)
    proposals = ground_facts({"destination": "Delhi"}, confidence=0.88,
                             source="vision", state_version=v1)
    snap, skipped = apply_verified(sm, "s_42", proposals)
    assert skipped == []
    assert snap.slots["destination"] == "Delhi"


def test_c_mixed_v1_v2_batch_only_v2_applies():
    sm = StateManager()
    v1 = _flight_v1(sm)
    old = ground_facts({"destination": "Delhi"}, confidence=0.88,
                       source="vision", state_version=v1)
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    new = ground_facts({"destination": "Mumbai"}, confidence=0.90,
                       source="vision", state_version=v2)

    snap, skipped = apply_verified(sm, "s_42", old + new)
    stale = [p for p in skipped if p.reason.startswith("stale_version_")]
    assert len(stale) == 1
    assert stale[0].candidate_value == "Delhi"
    assert snap.slots["destination"] == "Mumbai"


def test_unbound_legacy_proposals_still_apply():
    # Backwards compatibility: proposals without state_version keep the
    # pre-fix behavior (verified -> applied).
    sm = StateManager()
    _flight_v1(sm)
    proposals = ground_facts({"destination": "Delhi"}, confidence=0.88,
                             source="vision")
    assert all(p.state_version is None for p in proposals)
    snap, skipped = apply_verified(sm, "s_42", proposals)
    assert skipped == []
    assert snap.slots["destination"] == "Delhi"
