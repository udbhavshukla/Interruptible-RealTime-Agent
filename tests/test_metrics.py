import pytest

from src.evaluation.metrics import (
    MetricsCollector,
    ttfr,
    interruption_detection_latency,
    cancellation_latency,
    re_planning_latency,
    task_completion_rate,
    interruption_recovery_rate,
    stale_tool_calls,
    duplicate_state_changing_calls,
    state_snapshot_correctness,
    protocol_validity,
)
from src.evaluation.harness import load_scenario


# ---------------------------------------------------------------------------
# Legacy collector (kept for backward compatibility)
# ---------------------------------------------------------------------------
def test_basic_counts():
    mc = MetricsCollector()
    mc.add_event({"type": "user_say", "timestamp": 1.0})
    mc.add_event({"type": "user_say", "timestamp": 2.0})
    mc.add_event({"type": "interruption", "timestamp": 3.0})
    mc.add_event({"type": "stale_result", "timestamp": 4.0, "duration": 0.5})

    counts = mc.get_counts()
    assert counts["event_counts"]["user_say"] == 2
    assert counts["interruption_count"] == 1
    assert counts["stale_result_count"] == 1
    assert 0.5 in counts["durations"]


def test_clear():
    mc = MetricsCollector()
    mc.add_event({"type": "a", "timestamp": 0.0})
    mc.clear()
    counts = mc.get_counts()
    assert counts["event_counts"] == {}
    assert counts["interruption_count"] == 0
    assert counts["stale_result_count"] == 0
    assert counts["durations"] == []


# ---------------------------------------------------------------------------
# Latency metrics
# ---------------------------------------------------------------------------
def test_ttfr_normal():
    events = [
        {"type": "user_say", "timestamp": 1.0},
        {"type": "tool_result", "timestamp": 1.4},
    ]
    assert ttfr(events) == pytest.approx(0.4)


def test_ttfr_empty_input():
    assert ttfr([]) is None


def test_ttfr_missing_response():
    events = [{"type": "user_say", "timestamp": 1.0}]
    assert ttfr(events) is None


def test_ttfr_alternate_type_names():
    events = [
        {"type": "user_input", "timestamp": 0.0},
        {"type": "assistant_response", "timestamp": 0.25},
    ]
    assert ttfr(events) == pytest.approx(0.25)


def test_interruption_detection_latency_normal():
    events = [
        {"type": "interruption", "timestamp": 5.0},
        {"type": "interruption_detected", "timestamp": 5.3},
    ]
    assert interruption_detection_latency(events) == pytest.approx(0.3)


def test_interruption_detection_latency_empty():
    assert interruption_detection_latency([]) is None


def test_interruption_detection_latency_missing_end():
    events = [{"type": "interruption", "timestamp": 5.0}]
    assert interruption_detection_latency(events) is None


def test_cancellation_latency_normal():
    events = [
        {"type": "cancellation", "timestamp": 2.0},
        {"type": "cancelled", "timestamp": 2.75},
    ]
    assert cancellation_latency(events) == pytest.approx(0.75)


def test_cancellation_latency_empty():
    assert cancellation_latency([]) is None


def test_re_planning_latency_normal():
    events = [
        {"type": "replan", "timestamp": 10.0},
        {"type": "replan_done", "timestamp": 10.6},
    ]
    assert re_planning_latency(events) == pytest.approx(10.6 - 10.0)


def test_re_planning_latency_empty():
    assert re_planning_latency([]) is None


def test_latency_uses_first_occurrence_only():
    events = [
        {"type": "interruption", "timestamp": 1.0},
        {"type": "interruption_detected", "timestamp": 1.2},
        {"type": "interruption", "timestamp": 9.0},
        {"type": "interruption_detected", "timestamp": 9.9},
    ]
    # Only the first interruption/detection pair should be measured.
    assert interruption_detection_latency(events) == pytest.approx(1.2 - 1.0)


# ---------------------------------------------------------------------------
# Rate metrics
# ---------------------------------------------------------------------------
def test_task_completion_rate_partial():
    events = [
        {"type": "task_start", "timestamp": 0.0},
        {"type": "task_start", "timestamp": 1.0},
        {"type": "task_start", "timestamp": 2.0},
        {"type": "task_complete", "timestamp": 3.0},
    ]
    assert task_completion_rate(events) == pytest.approx(1 / 3)


def test_task_completion_rate_all_complete():
    events = [
        {"type": "task_start", "timestamp": 0.0},
        {"type": "task_complete", "timestamp": 1.0},
    ]
    assert task_completion_rate(events) == 1.0


def test_task_completion_rate_no_starts_is_zero():
    # Empty input (and no task_start events) must not raise ZeroDivisionError.
    assert task_completion_rate([]) == 0.0
    assert task_completion_rate([{"type": "task_complete", "timestamp": 1.0}]) == 0.0


def test_interruption_recovery_rate_partial():
    events = [
        {"type": "interruption", "timestamp": 1.0},
        {"type": "interruption_recovered", "timestamp": 1.5},
        {"type": "interruption", "timestamp": 5.0},
    ]
    assert interruption_recovery_rate(events) == 0.5


def test_interruption_recovery_rate_empty_is_zero():
    assert interruption_recovery_rate([]) == 0.0


# ---------------------------------------------------------------------------
# Count metrics
# ---------------------------------------------------------------------------
def test_stale_tool_calls_count():
    events = [
        {"type": "stale_result", "timestamp": 1.0},
        {"type": "tool_result", "timestamp": 2.0},
        {"type": "stale_result", "timestamp": 3.0},
    ]
    assert stale_tool_calls(events) == 2


def test_stale_tool_calls_empty():
    assert stale_tool_calls([]) == 0


def test_duplicate_state_changing_calls_counts_repeats():
    events = [
        {"type": "state_change", "timestamp": 1.0, "payload": {"action_id": "a"}},
        {"type": "state_change", "timestamp": 2.0, "payload": {"action_id": "b"}},
        {"type": "state_change", "timestamp": 3.0, "payload": {"action_id": "a"}},
        {"type": "state_change", "timestamp": 4.0, "payload": {"action_id": "a"}},
    ]
    # "a" appears 3 times -> 2 duplicates; "b" once -> 0.
    assert duplicate_state_changing_calls(events) == 2


def test_duplicate_state_changing_calls_ignores_events_without_id():
    events = [
        {"type": "state_change", "timestamp": 1.0, "payload": {}},
        {"type": "state_change", "timestamp": 2.0, "payload": {}},
    ]
    assert duplicate_state_changing_calls(events) == 0


def test_duplicate_state_changing_calls_empty():
    assert duplicate_state_changing_calls([]) == 0


# ---------------------------------------------------------------------------
# Correctness / validity checks
# ---------------------------------------------------------------------------
def test_state_snapshot_correctness_ok():
    events = [
        {"type": "state_snapshot", "timestamp": 1.0, "state": {"mode": "a"}},
        {"type": "state_snapshot", "timestamp": 2.0, "state": {"mode": "b"}},
    ]
    assert state_snapshot_correctness(events) is True


def test_state_snapshot_correctness_empty_is_false():
    assert state_snapshot_correctness([]) is False


def test_state_snapshot_correctness_out_of_order_is_false():
    events = [
        {"type": "state_snapshot", "timestamp": 5.0, "state": {"mode": "a"}},
        {"type": "state_snapshot", "timestamp": 1.0, "state": {"mode": "b"}},
    ]
    assert state_snapshot_correctness(events) is False


def test_state_snapshot_correctness_missing_field_is_false():
    events = [{"type": "state_snapshot", "timestamp": 1.0}]
    assert state_snapshot_correctness(events) is False


def test_protocol_validity_ok():
    events = [
        {"type": "user_say", "timestamp": 1.0},
        {"type": "tool_result", "timestamp": 2.0},
    ]
    assert protocol_validity(events) is True


def test_protocol_validity_empty_is_true():
    # Nothing to violate the protocol.
    assert protocol_validity([]) is True


def test_protocol_validity_unknown_type_is_false():
    events = [{"type": "made_up_type", "timestamp": 1.0}]
    assert protocol_validity(events) is False


def test_protocol_validity_missing_timestamp_is_false():
    events = [{"type": "user_say"}]
    assert protocol_validity(events) is False


def test_protocol_validity_custom_allowed_types():
    events = [{"type": "custom_event", "timestamp": 1.0}]
    assert protocol_validity(events, allowed_types={"custom_event"}) is True


# ---------------------------------------------------------------------------
# Project event vocabulary (scenarios/*.json): USER_INTERRUPT / STALE_REJECT
# ---------------------------------------------------------------------------
def test_collector_counts_user_interrupt():
    """MetricsCollector must count USER_INTERRUPT as an interruption."""
    mc = MetricsCollector()
    mc.add_event({"type": "USER_INPUT", "timestamp": 0.0})
    mc.add_event({"type": "USER_INTERRUPT", "timestamp": 0.6})

    counts = mc.get_counts()
    assert counts["interruption_count"] == 1
    assert counts["event_counts"]["USER_INTERRUPT"] == 1


def test_collector_counts_stale_reject():
    """MetricsCollector must count STALE_REJECT as a stale result."""
    mc = MetricsCollector()
    mc.add_event({"type": "TOOL_RESULT", "timestamp": 1.4})
    mc.add_event({"type": "STALE_REJECT", "timestamp": 2.2})

    counts = mc.get_counts()
    assert counts["stale_result_count"] == 1
    assert counts["event_counts"]["STALE_REJECT"] == 1


def test_ttfr_uses_project_vocabulary():
    # Mirrors scenarios/basic_task.json: first response is the FAST_ACK.
    events = [
        {"type": "USER_INPUT", "timestamp": 0.0},
        {"type": "FAST_ACK", "timestamp": 0.1},
        {"type": "TOOL_CALL", "timestamp": 0.2},
        {"type": "TOOL_RESULT", "timestamp": 1.0},
        {"type": "FINAL_RESPONSE", "timestamp": 1.2},
    ]
    assert ttfr(events) == pytest.approx(0.1)


def test_interruption_detection_latency_from_user_interrupt():
    # Mirrors scenarios/interruption.json: USER_INTERRUPT 0.6 -> CANCEL 0.7.
    events = [
        {"type": "TOOL_CALL", "timestamp": 0.2},
        {"type": "USER_INTERRUPT", "timestamp": 0.6},
        {"type": "CANCEL", "timestamp": 0.7},
    ]
    assert interruption_detection_latency(events) == pytest.approx(0.1)


def test_cancellation_latency_from_cancel_to_state_update():
    # Mirrors scenarios/interruption.json: CANCEL 0.7 -> STATE_UPDATE 0.8.
    events = [
        {"type": "USER_INTERRUPT", "timestamp": 0.6},
        {"type": "CANCEL", "timestamp": 0.7},
        {"type": "STATE_UPDATE", "timestamp": 0.8},
    ]
    assert cancellation_latency(events) == pytest.approx(0.1)


def test_stale_tool_calls_counts_stale_reject():
    """stale_tool_calls() must recognise STALE_REJECT by default."""
    events = [
        {"type": "TOOL_RESULT", "timestamp": 1.4},
        {"type": "TOOL_RESULT", "timestamp": 2.1},
        {"type": "STALE_REJECT", "timestamp": 2.2},
    ]
    assert stale_tool_calls(events) == 1


def test_protocol_validity_accepts_project_vocabulary():
    """All nine scenario event types must be valid by default."""
    events = [
        {"type": name, "timestamp": float(i)}
        for i, name in enumerate(
            [
                "USER_INPUT",
                "FAST_ACK",
                "TOOL_CALL",
                "TOOL_RESULT",
                "FINAL_RESPONSE",
                "USER_INTERRUPT",
                "CANCEL",
                "STATE_UPDATE",
                "STALE_REJECT",
            ]
        )
    ]
    assert protocol_validity(events) is True


def test_metrics_on_interruption_scenario():
    """Metrics over the real interruption scenario events."""
    events = load_scenario("interruption")["events"]
    mc = MetricsCollector()
    for ev in events:
        mc.add_event(ev)

    counts = mc.get_counts()
    assert counts["interruption_count"] == 1
    assert counts["stale_result_count"] == 0
    assert interruption_detection_latency(events) == pytest.approx(0.1)
    assert cancellation_latency(events) == pytest.approx(0.1)


def test_metrics_on_stale_result_scenario():
    """Metrics over the real stale_result scenario events."""
    events = load_scenario("stale_result")["events"]
    mc = MetricsCollector()
    for ev in events:
        mc.add_event(ev)

    counts = mc.get_counts()
    assert counts["interruption_count"] == 0
    assert counts["stale_result_count"] == 1
    assert stale_tool_calls(events) == 1


def test_ttfr_on_basic_task_scenario():
    """TTFR must compute from the real basic_task scenario events."""
    events = load_scenario("basic_task")["events"]
    assert ttfr(events) == pytest.approx(0.1)
