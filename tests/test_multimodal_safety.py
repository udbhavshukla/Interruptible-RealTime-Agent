"""Multimodal state-safety pins (Step 10 audit).

Ownership: perceive (stt/vision, pure data) -> propose (ground_facts) ->
verify (verify_slot, sole threshold gate) -> commit (apply_verified +
StateManager.update_slots, the ONLY multimodal->state path).
"""

import pytest

from src.multimodal import (
    Evidence,
    GroundedProposal,
    apply_verified,
    extract_facts,
    ground_facts,
    transcribe_wav,
    verify_slot,
)
from src.state import InvalidSlotError, StateManager, UnknownSessionError


def _flight_session(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    sm.update_slots(sid, {"origin": "Bangalore", "destination": "Delhi",
                          "date": "2026-09-25"})
    return sm.current_version(sid)


def test_perception_alone_never_mutates_state():
    sm = StateManager()
    v = _flight_session(sm)
    t = transcribe_wav(b"SAY:Actually Mumbai")
    r = extract_facts(b"OCR:device_model=ABC-123")
    assert t.text and r.facts  # perception produced output...
    snap = sm.get_state("s_42")  # ...but state is untouched.
    assert snap.state_version == v
    assert snap.slots["destination"] == "Delhi"


def test_divergent_flags_never_commit():
    """safe_to_commit=True with verified=False (or vice versa) must not
    commit even when version-bound to current -- the commit point enforces
    BOTH flags (regression: only safe_to_commit was checked)."""
    sm = StateManager()
    v = _flight_session(sm)
    bad1 = GroundedProposal(slot="destination", candidate_value="Delhi",
                            confidence=0.90, source="vision", verified=False,
                            safe_to_commit=True, reason="forged",
                            state_version=v)
    bad2 = GroundedProposal(slot="destination", candidate_value="Delhi",
                            confidence=0.90, source="vision", verified=True,
                            safe_to_commit=False, reason="forged",
                            state_version=v)
    snap, skipped = apply_verified(sm, "s_42", [bad1, bad2])
    assert len(skipped) == 2
    assert snap.slots["destination"] == "Delhi"  # test setup value...
    assert sm.current_version("s_42") == v  # ...and no new version written


def test_divergent_proposal_cannot_overwrite_newer_value():
    sm = StateManager()
    v = _flight_session(sm)
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    bad = GroundedProposal(slot="destination", candidate_value="Delhi",
                           confidence=0.90, source="vision", verified=False,
                           safe_to_commit=True, reason="forged",
                           state_version=v2)
    snap, skipped = apply_verified(sm, "s_42", [bad])
    assert len(skipped) == 1
    assert snap.slots["destination"] == "Mumbai"


def test_future_bound_proposal_skipped():
    sm = StateManager()
    v = _flight_session(sm)
    p = GroundedProposal(slot="destination", candidate_value="Delhi",
                         confidence=0.90, source="vision", verified=True,
                         safe_to_commit=True, reason="above_threshold",
                         state_version=v + 5)
    snap, skipped = apply_verified(sm, "s_42", [p])
    assert len(skipped) == 1
    assert skipped[0].reason.startswith("stale_version_")
    assert sm.current_version("s_42") == v


def test_apply_unknown_session_raises_structured():
    sm = StateManager()
    with pytest.raises(UnknownSessionError):
        apply_verified(sm, "ghost", [])


def test_batch_application_is_atomic():
    """One invalid slot aborts the whole batch: update_slots validates
    upfront, so valid proposals are NOT partially applied."""
    sm = StateManager()
    v = _flight_session(sm)
    good = ground_facts({"destination": "Goa"}, confidence=0.90,
                        source="vision", state_version=v)
    bad = GroundedProposal(slot="bogus_slot", candidate_value="x",
                           confidence=0.90, source="manual", verified=True,
                           safe_to_commit=True, reason="trusted_source",
                           state_version=v)
    with pytest.raises(InvalidSlotError):
        apply_verified(sm, "s_42", good + [bad])
    assert sm.get_state("s_42").slots["destination"] == "Delhi"
    assert sm.current_version("s_42") == v


def test_bound_proposal_applied_to_wrong_session_skipped():
    # Versions are per-session counters: move B to a different version so
    # A's binding demonstrably mismatches. (If both sessions sat at the
    # same counter value, a misrouted proposal would apply -- proposals
    # carry no session tag by design, so M2 must route session_id
    # correctly; the version gate is a second net, not the router.)
    sm = StateManager()
    va = _flight_session(sm, "s_A")
    _flight_session(sm, "s_B")
    sm.update_slot("s_B", "date", "2026-09-26")  # B moves past va
    bound_to_a = ground_facts({"destination": "Goa"}, confidence=0.90,
                              source="vision", state_version=va)
    snap_b, skipped = apply_verified(sm, "s_B", bound_to_a)
    assert len(skipped) == 1  # B is at a different version
    assert snap_b.slots["destination"] == "Delhi"
    assert sm.get_state("s_A").slots["destination"] == "Delhi"


def test_threshold_boundary_is_inclusive():
    assert verify_slot([Evidence(slot="d", value="X", confidence=0.75,
                                 source="vision")]).decision == "verified"
    assert verify_slot([Evidence(slot="d", value="X", confidence=0.74,
                                 source="vision")]).decision == "clarification"
