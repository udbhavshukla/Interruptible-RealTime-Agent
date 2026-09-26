"""Evaluation harness for Member 4.

Loads mock scenarios from the repository ``scenarios/`` directory, runs
structural and metric checks against a stream of events, and returns a
simple PASS/FAIL result.

The harness contains **no** agent, coordinator, state‑manager,
tool‑manager or multimodal logic.  When a real runtime exists it can be
connected later by passing a ``system`` callable (or an explicit
``observed_events`` list) to :func:`run_harness`; until then the
scenario's own mock events are evaluated against their expectations.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable, Optional, Sequence

from .metrics import MetricsCollector, protocol_validity
from .tracer import Tracer

# scenarios/ lives at the repository root:
# .../src/evaluation/harness.py -> parents[2] == repository root
SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------
def load_scenario(name_or_path: str | Path) -> dict:
    """Load a scenario JSON file.

    Accepts an explicit path, or a bare scenario name such as
    ``"basic_task"`` / ``"basic_task.json"``, resolved inside the
    repository ``scenarios/`` directory.
    """
    path = Path(name_or_path)
    if not path.is_file():
        name = path.name
        if not name.lower().endswith(".json"):
            name += ".json"
        path = SCENARIOS_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        scenario = json.load(fh)
    scenario.setdefault("id", path.stem)
    return scenario


def scenario_paths() -> list[Path]:
    """Return every scenario file in ``scenarios/``, sorted by name."""
    if not SCENARIOS_DIR.is_dir():
        return []
    return sorted(SCENARIOS_DIR.glob("*.json"))


def expected_event_order(scenario: dict) -> list[str]:
    """Expected event type sequence for a scenario.

    Uses the explicit ``expected_event_order`` key when present and falls
    back to the types of the scenario's own ``events``.
    """
    order = scenario.get("expected_event_order")
    if order:
        return list(order)
    return [ev.get("type") for ev in scenario.get("events", [])]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def check_order(observed_types: Sequence[Optional[str]],
                expected_types: Sequence[str]) -> bool:
    """Event order must match exactly (same types, same sequence)."""
    return list(observed_types) == list(expected_types)


def find_missing(observed_types: Sequence[Optional[str]],
                 expected_types: Sequence[str]) -> dict[str, int]:
    """Expected event types that are absent or under‑represented."""
    observed = Counter(observed_types)
    expected = Counter(expected_types)
    return {t: expected[t] - observed[t]
            for t in sorted(expected)
            if observed[t] < expected[t]}


def find_unexpected(observed_types: Sequence[Optional[str]],
                    expected_types: Sequence[str]) -> dict[str, int]:
    """Observed event types that were not expected or are over‑represented."""
    observed = Counter(observed_types)
    expected = Counter(expected_types)
    return {t: observed[t] - expected[t]
            for t in sorted(observed, key=str)
            if observed[t] > expected[t]}


def check_required_events(observed_types: Sequence[Optional[str]],
                          required_types: Sequence[str]) -> bool:
    """Every required event type must appear at least once."""
    observed = set(observed_types)
    return all(t in observed for t in required_types)


def check_metrics(scenario: dict,
                  observed_events: Sequence[dict]) -> tuple[bool, dict, dict]:
    """Compare ``expected_metrics`` with metrics computed by the metrics module.

    Returns ``(metrics_match, observed_metrics, mismatches)``.  When the
    scenario declares no ``expected_metrics`` the check is skipped and
    reported as matching.
    """
    collector = MetricsCollector()
    for ev in observed_events:
        collector.add_event(ev)
    observed_metrics = collector.get_counts()

    expected = scenario.get("expected_metrics")
    if not expected:
        return True, observed_metrics, {}

    mismatches = {
        key: {"expected": want, "observed": observed_metrics.get(key)}
        for key, want in expected.items()
        if observed_metrics.get(key) != want
    }
    return not mismatches, observed_metrics, mismatches


def check_protocol(observed_events: Sequence[dict],
                   expected_types: Sequence[str]) -> bool:
    """Every observed event must be well formed and use an expected type."""
    return protocol_validity(observed_events, allowed_types=set(expected_types))


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
def evaluate_events(scenario: dict, observed_events: Sequence[dict]) -> dict:
    """Run every check for *scenario* against *observed_events*.

    Returns a result dict with a ``status`` of ``"PASS"`` or ``"FAIL"``
    plus the individual check outcomes.
    """
    expected_order = expected_event_order(scenario)
    observed_order = [ev.get("type") for ev in observed_events]

    required = list(scenario.get("required_events") or expected_order)

    order_ok = check_order(observed_order, expected_order)
    required_ok = check_required_events(observed_order, required)
    missing = find_missing(observed_order, expected_order)
    unexpected = find_unexpected(observed_order, expected_order)
    protocol_ok = check_protocol(observed_events, expected_order)
    metrics_ok, observed_metrics, mismatches = check_metrics(scenario, observed_events)

    passed = (
        order_ok
        and required_ok
        and not missing
        and not unexpected
        and protocol_ok
        and metrics_ok
    )

    return {
        "scenario_id": scenario.get("id"),
        "scenario_description": scenario.get("description"),
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "checks": {
            "order_match": order_ok,
            "required_events_present": required_ok,
            "missing_events": missing,
            "unexpected_events": unexpected,
            "protocol_valid": protocol_ok,
            "metrics_match": metrics_ok,
            "metric_mismatches": mismatches,
        },
        "expected_order": expected_order,
        "observed_order": observed_order,
        "observed_events": list(observed_events),
        "metrics": observed_metrics,
        "expected_metrics": scenario.get("expected_metrics"),
        # Backward-compatible alias: did the metrics match?
        "matches": metrics_ok,
    }


def run_harness(scenario_path: str | Path,
                *,
                tracer: Optional[Tracer] = None,
                metrics: Optional[MetricsCollector] = None,
                system: Optional[Callable[[dict], Sequence[dict]]] = None,
                observed_events: Optional[Sequence[dict]] = None) -> dict:
    """Load a scenario and evaluate a stream of events against it.

    The event source is resolved in this order:

    1. ``observed_events`` – an explicit event list (e.g. replayed trace).
    2. ``system`` – a callable taking the loaded scenario and returning
       the events it produced.  This is the hook a real agent/runtime
       will implement later; the harness never inspects its internals.
    3. otherwise the scenario's own mock ``events`` (self‑evaluation).

    Events are recorded through the supplied (or a fresh) ``Tracer`` and
    counted by a ``MetricsCollector``, then all checks are run.
    """
    scenario = load_scenario(scenario_path)

    if observed_events is None:
        if system is not None:
            observed_events = list(system(scenario))
        else:
            observed_events = list(scenario.get("events", []))

    t = tracer or Tracer()
    m = metrics or MetricsCollector()
    for ev in observed_events:
        # Events missing required fields are reported by the protocol
        # check instead of crashing the tracer.
        if "type" in ev and "timestamp" in ev:
            t.record(ev)
        m.add_event(ev)

    result = evaluate_events(scenario, observed_events)
    return result


def evaluate_scenario(name_or_path: str | Path,
                      **kwargs) -> dict:
    """Convenience wrapper: load a scenario (by name or path) and evaluate it."""
    return run_harness(name_or_path, **kwargs)


def evaluate_all_scenarios() -> list[dict]:
    """Evaluate every scenario found in the ``scenarios/`` directory."""
    return [run_harness(path) for path in scenario_paths()]
