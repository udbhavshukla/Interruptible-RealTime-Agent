"""Protocol schema invariants (Step 12 audit pins).

- Every defined type has constructor coverage (incl. filler + manifest).
- Payloads are isolated at construction and at to_dict (no aliasing).
- The (session_id, call_id, state_version) triple plus tool/error/
  commit/replay fields survive layer crossings intact.
"""

from src.protocol import (
    AUDIO_WAV,
    CANCEL_CALL,
    CLARIFICATION,
    END_OF_TURN,
    FILLER,
    FINAL_RESPONSE,
    IMAGE_PNG,
    INTERRUPT,
    STATE_SNAPSHOT,
    TEXT_CHUNK,
    TOOL_CALL,
    TOOL_MANIFEST,
    TOOL_RESULT,
    Action,
    Event,
    audio_event,
    cancel_action,
    clarification_action,
    end_of_turn,
    filler_action,
    final_response_action,
    image_event,
    interrupt_event,
    snapshot_action,
    text_chunk,
    tool_call_action,
    tool_manifest_event,
    tool_result_event,
)


def test_every_defined_type_has_constructor_coverage():
    assert text_chunk("s", "hi").type == TEXT_CHUNK
    assert end_of_turn("s").type == END_OF_TURN
    assert audio_event("s", b"1234").type == AUDIO_WAV
    assert image_event("s", b"1234").type == IMAGE_PNG
    assert interrupt_event("s").type == INTERRUPT
    assert tool_result_event("s", "c", 1, {}).type == TOOL_RESULT
    assert tool_manifest_event("s", {"name": "t"}).type == TOOL_MANIFEST
    assert filler_action("s", "one moment").type == FILLER
    assert tool_call_action("s", "c", "t", {}, 1).type == TOOL_CALL
    assert cancel_action("s", "c").type == CANCEL_CALL
    assert clarification_action("s", "slot", "why").type == CLARIFICATION
    assert final_response_action("s", "done", 1).type == FINAL_RESPONSE
    assert snapshot_action("s", {"state_version": 3}).type == STATE_SNAPSHOT


def test_payload_isolated_at_construction_and_serialization():
    args = {"city": "Goa", "nested": {"x": 1}}
    a = tool_call_action("s", "c1", "hotel_search", args, 1)
    args["city"] = "HACKED"
    args["nested"]["x"] = 999
    assert a.payload["args"] == {"city": "Goa", "nested": {"x": 1}}
    d = a.to_dict()
    d["payload"]["args"]["city"] = "HACKED2"
    assert a.payload["args"]["city"] == "Goa"  # to_dict also copies

    raw = {"manifest": {"name": "t"}}
    e = tool_manifest_event("s", raw["manifest"])
    raw["manifest"]["name"] = "HACKED"
    assert e.payload["manifest"] == {"name": "t"}


def test_tool_call_and_result_carry_binding_triple():
    a = tool_call_action("s_42", "call_001", "flight_search",
                         {"origin": "B"}, state_version=7)
    assert (a.session_id, a.call_id, a.state_version) == ("s_42", "call_001", 7)
    assert a.payload["tool_name"] == "flight_search"
    d = a.to_dict()
    assert d["call_id"] == "call_001" and d["state_version"] == 7

    envelope = {"call_id": "call_001", "state_version": 7, "status": "committed",
                "committed": True, "idempotent_replay": False,
                "payload": {"booking_id": "b1"}}
    evt = tool_result_event("s_42", "call_001", 7, envelope)
    p = evt.payload
    assert (evt.session_id, p["call_id"], p["state_version"]) == ("s_42", "call_001", 7)
    # Commit/error/replay fields survive inside the forwarded envelope.
    assert p["result"]["committed"] is True
    assert p["result"]["payload"] == {"booking_id": "b1"}


def test_ids_timestamps_and_versionless_actions():
    e1, e2 = text_chunk("s", "a"), text_chunk("s", "b")
    assert e1.event_id != e2.event_id and e1.ts > 0
    f = filler_action("s", "one moment")  # no version binding by design
    assert f.call_id is None and f.state_version is None
    assert "call_id" not in f.to_dict() and "state_version" not in f.to_dict()
    c = cancel_action("s", "c1")  # cancellation needs session + call only
    assert c.call_id == "c1" and c.state_version is None


def test_clarification_and_snapshot_shapes():
    c = clarification_action("s", slot="device_model",
                             reason="low_confidence", options=["A", "B"])
    assert c.payload == {"slot": "device_model", "reason": "low_confidence",
                         "options": ["A", "B"]}
    snap = {"session_id": "s", "state_version": 4, "slots": {"a": 1}}
    s = snapshot_action("s", snap)
    snap["slots"]["a"] = 999
    assert s.state_version == 4
    assert s.payload["snapshot"]["slots"] == {"a": 1}


def test_events_are_immutable_handles():
    import dataclasses

    e = text_chunk("s", "hi")
    assert isinstance(e, Event) and isinstance(
        tool_call_action("s", "c", "t", {}, 1), Action)
    # Frozen dataclass blocks attribute reassignment (same policy as
    # StateSnapshot: construction-time isolation, not post-hoc freezing
    # of the payload dict itself).
    with_blocks = False
    try:
        e.type = "forged"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        with_blocks = True
    assert with_blocks
