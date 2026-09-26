"""Regression tests for the evaluation harness (Member 4).

These encode, as automated tests, the negative-case behaviour that was
previously only verified manually: a malformed or incorrect event stream
must produce FAIL, while a valid scenario must produce PASS.

The tests use only the existing harness interfaces
(``load_scenario`` / ``evaluate_events`` / ``run_harness`` /
``evaluate_all_scenarios``) and no external dependencies.
"""

import copy

from src.evaluation.harness import (
    evaluate_all_scenarios,
    evaluate_events,
    load_scenario,
    run_harness,
)


def _failing_checks(result: dict) -> list[str]:
    """Names of checks that indicate failure (``False`` or a non-empty dict)."""
    return [
        name
        for name, value in result["checks"].items()
        if value is False or (isinstance(value, dict) and value)
    ]


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------
def test_valid_scenario_passes():
    """A complete, correctly ordered basic_task scenario must PASS."""
    result = run_harness("basic_task")
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert _failing_checks(result) == []


def test_all_scenarios_pass():
    """Every checked-in scenario must PASS against its own expectations."""
    results = evaluate_all_scenarios()
    assert len(results) == 3
    for result in results:
        assert result["status"] == "PASS", (
            f"{result['scenario_id']} failed checks: {_failing_checks(result)}"
        )


# ---------------------------------------------------------------------------
# Negative cases (1–7)
# ---------------------------------------------------------------------------
def test_missing_final_response_fails():
    """1. Dropping FINAL_RESPONSE must FAIL with a missing-event report."""
    scenario = load_scenario("basic_task")
    events = scenario["events"][:-1]  # remove the final response

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["passed"] is False
    assert result["checks"]["order_match"] is False
    assert result["checks"]["missing_events"] == {"FINAL_RESPONSE": 1}
    assert result["checks"]["required_events_present"] is False


def test_reversed_event_order_fails():
    """2. Correct events in the wrong order must FAIL."""
    scenario = load_scenario("basic_task")
    events = list(reversed(scenario["events"]))

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["order_match"] is False
    # Same multiset of events: no missing or unexpected types here.
    assert result["checks"]["missing_events"] == {}
    assert result["checks"]["unexpected_events"] == {}


def test_unknown_extra_event_fails():
    """3. An event type outside the scenario vocabulary must FAIL."""
    scenario = load_scenario("basic_task")
    events = scenario["events"] + [{"type": "HALLUCINATION", "timestamp": 9.9}]

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["unexpected_events"] == {"HALLUCINATION": 1}
    assert result["checks"]["protocol_valid"] is False
    assert result["checks"]["order_match"] is False


def test_missing_timestamp_fails():
    """4. An event without a timestamp must FAIL the protocol check."""
    scenario = load_scenario("basic_task")
    events = copy.deepcopy(scenario["events"])
    del events[0]["timestamp"]

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["protocol_valid"] is False


def test_interruption_without_user_interrupt_fails():
    """5. An interruption run that never emits USER_INTERRUPT must FAIL."""
    scenario = load_scenario("interruption")
    events = [
        ev for ev in scenario["events"] if ev["type"] != "USER_INTERRUPT"
    ]

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["missing_events"] == {"USER_INTERRUPT": 1}
    assert result["checks"]["required_events_present"] is False
    assert result["checks"]["metrics_match"] is False
    assert result["checks"]["metric_mismatches"]["interruption_count"] == {
        "expected": 1,
        "observed": 0,
    }


def test_stale_result_without_stale_reject_fails():
    """6. A stale result that is never rejected must FAIL."""
    scenario = load_scenario("stale_result")
    events = [ev for ev in scenario["events"] if ev["type"] != "STALE_REJECT"]

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["missing_events"] == {"STALE_REJECT": 1}
    assert result["checks"]["required_events_present"] is False
    assert result["checks"]["metrics_match"] is False
    assert result["checks"]["metric_mismatches"]["stale_result_count"] == {
        "expected": 1,
        "observed": 0,
    }


def test_duplicated_tool_result_fails():
    """7. An extra duplicate TOOL_RESULT must FAIL as unexpected."""
    scenario = load_scenario("basic_task")
    events = scenario["events"] + [copy.deepcopy(scenario["events"][3])]

    result = evaluate_events(scenario, events)

    assert result["status"] == "FAIL"
    assert result["checks"]["unexpected_events"] == {"TOOL_RESULT": 1}
    assert result["checks"]["order_match"] is False
