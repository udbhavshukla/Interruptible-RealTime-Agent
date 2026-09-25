"""Multimodal tests: STT, vision, verification, grounding."""

from src.multimodal import (
    Evidence,
    apply_verified,
    extract_facts,
    filter_committable,
    ground_facts,
    transcribe_wav,
    verify_slot,
)
from src.state import StateManager


def test_wav_to_transcript():
    t = transcribe_wav(b"SAY:Find a flight from Bangalore to Delhi")
    assert t.text == "Find a flight from Bangalore to Delhi"
    assert t.source == "audio"


def test_transcript_confidence():
    t = transcribe_wav(b"SAY:hello")
    assert 0.0 <= t.confidence <= 1.0
    assert t.confidence >= 0.75
    empty = transcribe_wav(b"")
    assert empty.confidence == 0.0


def test_png_to_candidate_facts():
    r = extract_facts(b"OCR:device_model=ABC-123;error_code=E42")
    assert r.facts == {"device_model": "ABC-123", "error_code": "E42"}
    assert r.source == "image"


def test_visual_confidence_reported():
    r = extract_facts(b"OCR:device_model=ABC-123")
    assert 0.0 <= r.confidence <= 1.0


def test_low_confidence_requests_clarification():
    r = extract_facts(b"LOWCONF:device_model=ABC-123")
    assert r.confidence < 0.75
    outcome = verify_slot([Evidence(slot="device_model",
                                    value=r.facts["device_model"],
                                    confidence=r.confidence,
                                    source="vision")])
    assert outcome.decision == "clarification"


def test_verified_fact_accepted():
    outcome = verify_slot([Evidence(slot="device_model", value="XYZ-987",
                                    confidence=0.95, source="manual")])
    assert outcome.decision == "verified"
    assert outcome.value == "XYZ-987"


def test_conflicting_facts_detected():
    outcome = verify_slot([
        Evidence(slot="device_model", value="ABC-123",
                 confidence=0.88, source="vision"),
        Evidence(slot="device_model", value="XYZ-987",
                 confidence=0.95, source="manual"),
    ])
    assert outcome.decision == "conflict"
    assert len(outcome.conflicting) == 2


def test_unverified_visual_fact_cannot_overwrite_trusted_state():
    sm = StateManager()
    sm.create_session("s")
    sm.update_intent("s", "support_ticket")
    sm.update_slots("s", {"device_model": "XYZ-987", "error_code": "E42"})
    v0 = sm.current_version("s")

    # Low-confidence visual guess proposes a different model.
    r = extract_facts(b"LOWCONF:device_model=ABC-123")
    proposals = ground_facts(r.facts, confidence=r.confidence, source="vision")
    assert all(p.safe_to_commit is False for p in proposals)
    snap, skipped = apply_verified(sm, "s", proposals)
    assert len(skipped) == 1
    # Trusted state untouched, no version bump from skipped proposals.
    assert sm.get_state("s").slots["device_model"] == "XYZ-987"
    assert sm.current_version("s") == v0


def test_verified_proposal_commits():
    sm = StateManager()
    sm.create_session("s")
    sm.update_intent("s", "support_ticket")
    proposals = ground_facts({"device_model": "XYZ-987"},
                             confidence=0.95, source="manual")
    assert filter_committable(proposals)
    snap, skipped = apply_verified(sm, "s", proposals)
    assert skipped == []
    assert snap.slots["device_model"] == "XYZ-987"


def test_text_only_fallback_works():
    # Empty multimodal input degrades gracefully; text path unaffected.
    assert transcribe_wav(b"").text == ""
    assert extract_facts(b"").facts == {}
    sm = StateManager()
    sm.create_session("s")
    sm.update_intent("s", "flight_search")
    snap = sm.update_slot("s", "destination", "Mumbai")
    assert snap.slots["destination"] == "Mumbai"


def test_multimodal_failure_does_not_crash_agent():
    # Garbage bytes produce low-confidence placeholders, never exceptions.
    t = transcribe_wav(bytes([0, 255, 0, 128, 1, 2, 3]))
    r = extract_facts(bytes([137, 80, 78, 71, 0, 1, 2, 3]))
    assert isinstance(t.text, str)
    assert isinstance(r.facts, dict)
    outcome = verify_slot([Evidence(slot="device_model",
                                    value=str(r.facts.get("image_hash", "?")),
                                    confidence=r.confidence, source="vision")])
    assert outcome.decision in ("clarification", "verified", "conflict")
