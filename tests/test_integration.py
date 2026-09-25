"""Protocol + end-to-end interruption integration tests (T0-T9)."""

import asyncio

from src.multimodal import (
    apply_verified,
    extract_facts,
    ground_facts,
    transcribe_wav,
)
from src.protocol import (
    PROTOCOL_VERSION,
    clarification_action,
    final_response_action,
    interrupt_event,
    text_chunk,
    tool_call_action,
    tool_result_event,
)
from src.state import StateManager
from src.tools import make_mock_registry


def run(coro):
    return asyncio.run(coro)


def test_protocol_events_have_ids_and_timestamps():
    e = text_chunk("s", "hello")
    assert e.event_id and e.ts > 0
    i = interrupt_event("s", "Actually Mumbai.")
    assert i.type == "interrupt"
    assert PROTOCOL_VERSION == "1.0"


def test_protocol_tool_call_binds_version():
    a = tool_call_action("s", "call_001", "flight_search",
                         {"origin": "B"}, state_version=1)
    assert a.call_id == "call_001"
    assert a.state_version == 1


def test_clarification_and_final_response_shapes():
    c = clarification_action("s", slot="device_model",
                             reason="low_confidence_0.61_below_0.75")
    assert c.payload["slot"] == "device_model"
    f = final_response_action("s", "Done", state_version=2)
    assert f.payload["text"] == "Done"


def test_full_interruption_flow_with_stale_rejection():
    """T0-T9: user -> state v1 -> call_001 -> interrupt -> state v2 ->
    call_002 -> stale call_001 result rejected -> call_002 accepted."""
    sm = StateManager()
    reg = make_mock_registry()

    # T0/T1: "Find a flight from Bangalore to Delhi."
    sm.create_session("s_42")
    sm.update_intent("s_42", "flight_search")
    sm.update_slots("s_42", {"origin": "Bangalore", "destination": "Delhi",
                             "date": "2026-09-25"})
    v1 = sm.current_version("s_42")

    # T2: plan call_001 against v1 (state + registry agree).
    sm.register_call("s_42", "call_001", state_version=v1,
                     tool_name="flight_search")
    a1 = tool_call_action("s_42", "call_001", "flight_search",
                          dict(sm.get_state("s_42").slots), v1)

    async def old_call():
        return await reg.execute(
            tool_name="flight_search", args=dict(sm.get_state("s_42").slots),
            call_id="call_001", session_id="s_42", state_version=v1)

    # T3/T4: "Actually Mumbai." -> localized correction -> v2.
    sm.update_slot("s_42", "destination", "Mumbai")
    v2 = sm.current_version("s_42")
    assert v2 == v1 + 1
    assert sm.get_state("s_42").slots["origin"] == "Bangalore"  # preserved

    # T5/T6: call_001 obsolete; plan call_002 against v2.
    sm.remove_call("s_42", "call_001")
    sm.register_call("s_42", "call_002", state_version=v2,
                     tool_name="flight_search")

    # T7: old result arrives -> REJECTED (stale version + inactive call).
    stale_envelope = {"call_id": "call_001", "state_version": v1,
                      "payload": {"destination": "Delhi"}}
    evt = tool_result_event("s_42", "call_001", v1, stale_envelope)
    assert sm.is_stale("s_42", evt.payload["state_version"],
                       call_id=evt.payload["call_id"]) is True
    # State must NOT be touched by the stale result.
    assert sm.get_state("s_42").slots["destination"] == "Mumbai"

    # T8: new result arrives -> accepted.
    async def new_call():
        return await reg.execute(
            tool_name="flight_search", args=dict(sm.get_state("s_42").slots),
            call_id="call_002", session_id="s_42", state_version=v2)

    res2 = run(new_call())
    assert res2["status"] == "completed"
    assert sm.is_stale("s_42", res2["state_version"],
                       call_id="call_002") is False
    assert res2["payload"]["destination"] == "Mumbai"

    # T9: final state + final response reference v2 / Mumbai.
    final = sm.get_state("s_42")
    assert final.slots["destination"] == "Mumbai"
    resp = final_response_action("s_42", "Mumbai flights found", v2)
    assert resp.state_version == v2


def test_multimodal_grounded_booking_correction_flow():
    """Vision proposes -> verified via manual lookup -> state commits."""
    sm = StateManager()
    sm.create_session("s_ticket")
    sm.update_intent("s_ticket", "support_ticket")

    seen = extract_facts(b"OCR:device_model=ABC-123;error_code=E42")
    proposals = ground_facts(seen.facts, confidence=seen.confidence,
                             source="vision")
    assert all(p.verified for p in proposals)  # 0.88 >= threshold
    snap, _ = apply_verified(sm, "s_ticket", proposals)
    assert snap.slots["device_model"] == "ABC-123"

    # Voice correction transcribes and overwrites via normal slot update.
    t = transcribe_wav(b"SAY:Actually error E43")
    assert "E43" in t.text
    snap2 = sm.update_slot("s_ticket", "error_code", "E43")
    assert snap2.slots["error_code"] == "E43"
    assert snap2.slots["device_model"] == "ABC-123"  # preserved
