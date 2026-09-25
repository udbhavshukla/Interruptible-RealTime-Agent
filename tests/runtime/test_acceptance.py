"""Tests for the Step 6 acceptance gate (aura.runtime.acceptance).

Fully deterministic: synchronous decision tests against registry records,
plus a small set of asyncio integration tests for the cancellation-race
cases. No sleeps anywhere.
"""

import asyncio
import dataclasses
import unittest

from aura.runtime.acceptance import AcceptanceGate, Decision, GateDecision
from aura.runtime.cancellation import CancellationManager, CancelOutcome
from aura.runtime.registry import TaskRegistry, TaskState
from aura.runtime.supervisor import TaskSupervisor

CALL = "call_001"


def build_registry(
    call_id: str = CALL,
    spawn_version: int = 1,
    state: TaskState = TaskState.SUCCEEDED,
) -> TaskRegistry:
    """A registry with one record driven to ``state`` (read-only afterwards)."""
    registry = TaskRegistry()
    registry.register(
        call_id=call_id, tool_name="flight_search", spawn_version=spawn_version
    )
    if state is TaskState.CREATED:
        return registry

    registry.mark_running(call_id)
    if state is TaskState.RUNNING:
        return registry
    if state is TaskState.CANCEL_REQUESTED:
        registry.request_cancel(call_id)
        return registry
    if state is TaskState.CANCELLED:
        registry.request_cancel(call_id)
        registry.mark_cancelled(call_id)
        return registry
    if state is TaskState.FAILED:
        registry.mark_failed(call_id)
        return registry
    registry.mark_succeeded(call_id)  # default: SUCCEEDED
    return registry


class CoreDecisionTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry()
        self.gate = AcceptanceGate(self.registry)

    def test_gate_exposes_injected_registry(self):
        self.assertIs(self.gate.registry, self.registry)

    def test_matching_version_is_accepted(self):
        decision = self.gate.evaluate(CALL, current_version=1)
        self.assertIs(decision.decision, Decision.ACCEPTED)
        self.assertEqual(decision.call_id, CALL)
        self.assertEqual(decision.spawn_version, 1)
        self.assertEqual(decision.current_version, 1)
        self.assertIs(decision.state, TaskState.SUCCEEDED)
        self.assertFalse(decision.cancel_requested)
        self.assertTrue(decision.reason)  # explains why

    def test_old_version_is_stale(self):
        decision = self.gate.evaluate(CALL, current_version=2)
        self.assertIs(decision.decision, Decision.STALE)
        self.assertEqual(decision.spawn_version, 1)
        self.assertEqual(decision.current_version, 2)
        self.assertIn("spawn_version 1", decision.reason)
        self.assertIn("version 2", decision.reason)

    def test_unknown_call_returns_unknown_call_decision(self):
        decision = self.gate.evaluate("ghost", current_version=1)
        self.assertIs(decision.decision, Decision.UNKNOWN_CALL)
        self.assertEqual(decision.call_id, "ghost")  # traceable
        self.assertIsNone(decision.spawn_version)
        self.assertIsNone(decision.state)
        self.assertEqual(decision.current_version, 1)
        self.assertTrue(decision.reason)

    def test_cancelled_task_is_rejected(self):
        registry = build_registry(state=TaskState.CANCELLED)
        gate = AcceptanceGate(registry)

        decision = gate.evaluate(CALL, current_version=1)  # same version
        self.assertIs(decision.decision, Decision.CANCELLED)
        self.assertIs(decision.state, TaskState.CANCELLED)
        self.assertTrue(decision.cancel_requested)

    def test_cancel_requested_but_not_settled_is_rejected(self):
        registry = build_registry(state=TaskState.CANCEL_REQUESTED)
        gate = AcceptanceGate(registry)

        decision = gate.evaluate(CALL, current_version=1)
        self.assertIs(decision.decision, Decision.CANCELLED)
        self.assertIs(decision.state, TaskState.CANCEL_REQUESTED)

    def test_successful_old_task_is_still_stale(self):
        # SUCCEEDED must never launder staleness.
        decision = self.gate.evaluate(CALL, current_version=2)
        self.assertIs(decision.decision, Decision.STALE)
        self.assertNotEqual(decision, Decision.ACCEPTED)
        self.assertIsNot(decision.decision, Decision.ACCEPTED)

    def test_stale_wins_over_cancellation(self):
        # The T9/T10 race: cancelled AND outdated -> STALE is the verdict,
        # while the cancellation facts remain visible in the decision.
        registry = build_registry(state=TaskState.CANCEL_REQUESTED)
        gate = AcceptanceGate(registry)

        decision = gate.evaluate(CALL, current_version=2)
        self.assertIs(decision.decision, Decision.STALE)
        self.assertTrue(decision.cancel_requested)  # still explained
        self.assertIs(decision.state, TaskState.CANCEL_REQUESTED)

    def test_ignored_cancel_and_success_still_stale(self):
        # Tool ignored cancellation and succeeded; session already moved on.
        registry = TaskRegistry()
        registry.register(call_id=CALL, tool_name="t", spawn_version=1)
        registry.mark_running(CALL)
        registry.request_cancel(CALL)
        registry.mark_succeeded(CALL)  # SUCCEEDED with cancel_requested=True

        gate = AcceptanceGate(registry)
        decision = gate.evaluate(CALL, current_version=2)
        self.assertIs(decision.decision, Decision.STALE)
        self.assertTrue(decision.cancel_requested)
        self.assertIs(decision.state, TaskState.SUCCEEDED)

    def test_newer_plan_new_call_is_accepted_while_old_is_stale(self):
        # Scenario T7/T8/T11/T12: call_002 spawns at v2 and is accepted,
        # while the v1 call_001 stays rejected.
        registry = build_registry(call_id="call_001", spawn_version=1)
        registry.register(call_id="call_002", tool_name="t", spawn_version=2)
        registry.mark_running("call_002")
        registry.mark_succeeded("call_002")
        gate = AcceptanceGate(registry)

        stale = gate.evaluate("call_001", current_version=2)
        accepted = gate.evaluate("call_002", current_version=2)
        self.assertIs(stale.decision, Decision.STALE)
        self.assertIs(accepted.decision, Decision.ACCEPTED)

    def test_current_version_without_cancel_is_accepted(self):
        registry = build_registry(spawn_version=3, state=TaskState.SUCCEEDED)
        gate = AcceptanceGate(registry)
        decision = gate.evaluate(CALL, current_version=3)
        self.assertIs(decision.decision, Decision.ACCEPTED)
        self.assertFalse(decision.cancel_requested)


class InvalidInputTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry()
        self.gate = AcceptanceGate(self.registry)

    def test_negative_current_version_is_invalid(self):
        decision = self.gate.evaluate(CALL, current_version=-1)
        self.assertIs(decision.decision, Decision.INVALID)
        self.assertIn("current_version", decision.reason)

    def test_non_integer_current_version_is_invalid(self):
        for bad in ("2", 1.5, None, True, [2]):
            with self.subTest(bad=bad):
                decision = self.gate.evaluate(CALL, current_version=bad)
                self.assertIs(decision.decision, Decision.INVALID)
                self.assertIn(repr(bad), decision.reason)

    def test_malformed_call_id_is_invalid(self):
        for bad in ("", None, 42):
            with self.subTest(bad=bad):
                decision = self.gate.evaluate(bad, current_version=1)
                self.assertIs(decision.decision, Decision.INVALID)
                self.assertIn("call_id", decision.reason)

    def test_unknown_wellformed_id_is_not_invalid(self):
        decision = self.gate.evaluate("no_such_call", current_version=1)
        self.assertIs(decision.decision, Decision.UNKNOWN_CALL)

    def test_failed_call_has_no_result(self):
        registry = build_registry(state=TaskState.FAILED)
        gate = AcceptanceGate(registry)
        decision = gate.evaluate(CALL, current_version=1)
        self.assertIs(decision.decision, Decision.INVALID)
        self.assertIn("no result", decision.reason)
        self.assertIs(decision.state, TaskState.FAILED)

    def test_running_call_cannot_be_accepted_yet(self):
        registry = build_registry(state=TaskState.RUNNING)
        gate = AcceptanceGate(registry)
        decision = gate.evaluate(CALL, current_version=1)
        self.assertIs(decision.decision, Decision.INVALID)
        self.assertIs(decision.state, TaskState.RUNNING)


class ImmutabilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry(spawn_version=1, state=TaskState.SUCCEEDED)
        self.gate = AcceptanceGate(self.registry)

    def test_decision_is_frozen(self):
        decision = self.gate.evaluate(CALL, current_version=2)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.decision = Decision.ACCEPTED  # type: ignore[misc]

    def test_evaluation_does_not_mutate_registry(self):
        record = self.registry.get(CALL)
        before = dataclasses.asdict(record)

        self.gate.evaluate(CALL, current_version=2)  # a rejecting decision
        self.gate.evaluate(CALL, current_version=1)  # and an accepting one

        self.assertEqual(dataclasses.asdict(record), before)
        self.assertEqual(len(self.registry), 1)

    def test_evaluation_is_deterministic(self):
        first = self.gate.evaluate(CALL, current_version=2)
        second = self.gate.evaluate(CALL, current_version=2)
        self.assertEqual(first, second)
        self.assertEqual(first.reason, second.reason)

    def test_decision_is_traceable(self):
        decision = self.gate.evaluate(CALL, current_version=2)
        self.assertEqual(
            sorted(
                f.name
                for f in dataclasses.fields(GateDecision)
            ),
            [
                "call_id",
                "cancel_requested",
                "current_version",
                "decision",
                "reason",
                "spawn_version",
                "state",
            ],
        )
        self.assertEqual(decision.call_id, CALL)
        self.assertEqual(decision.spawn_version, 1)
        self.assertEqual(decision.current_version, 2)
        self.assertIs(decision.state, TaskState.SUCCEEDED)
        self.assertFalse(decision.cancel_requested)
        self.assertTrue(decision.reason)


class CancellationRaceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end with a live supervisor: cancellation never buys acceptance."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)
        self.canceller = CancellationManager(self.supervisor)
        self.gate = AcceptanceGate(self.registry)

    async def test_cancellation_race_stale_protection(self):
        started = asyncio.Event()
        stop = asyncio.Event()

        async def stubborn():
            started.set()
            try:
                await stop.wait()
            except asyncio.CancelledError:
                pass  # ignores cancellation and returns anyway
            return "delhi-result"

        task = self.supervisor.start(
            CALL, stubborn, tool_name="flight_search", spawn_version=1
        )
        await asyncio.wait_for(started.wait(), timeout=2)

        # T3-T5: interrupt -> cancel requested while session is still v1.
        self.assertEqual(
            self.canceller.request(CALL), CancelOutcome.REQUESTED
        )

        # Mid-flight, with the session already moved to v2 (T6-T7):
        mid = self.gate.evaluate(CALL, current_version=2)
        self.assertIs(mid.decision, Decision.STALE)  # rejected before settling
        self.assertTrue(mid.cancel_requested)
        self.assertIs(mid.state, TaskState.CANCEL_REQUESTED)
        self.assertFalse(task.done())

        # T9-T10: the late result arrives anyway -> still STALE, never ACCEPTED.
        value = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(value, "delhi-result")  # cancellation was too late
        late = self.gate.evaluate(CALL, current_version=2)
        self.assertIs(late.decision, Decision.STALE)
        self.assertIs(late.state, TaskState.SUCCEEDED)
        self.assertTrue(late.cancel_requested)

    async def test_current_version_call_is_accepted_end_to_end(self):
        async def ok():
            return "mumbai-result"

        task = self.supervisor.start(
            CALL, ok, tool_name="flight_search", spawn_version=2
        )
        await asyncio.wait_for(task, timeout=2)

        decision = self.gate.evaluate(CALL, current_version=2)
        self.assertIs(decision.decision, Decision.ACCEPTED)
        self.assertIs(decision.state, TaskState.SUCCEEDED)
        self.assertEqual(self.supervisor.result(CALL), "mumbai-result")

    async def test_cancelled_current_version_call_is_rejected(self):
        started = asyncio.Event()
        stop = asyncio.Event()

        async def work():
            started.set()
            await stop.wait()

        task = self.supervisor.start(
            CALL, work, tool_name="flight_search", spawn_version=1
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        self.canceller.request(CALL)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        decision = self.gate.evaluate(CALL, current_version=1)
        self.assertIs(decision.decision, Decision.CANCELLED)
        self.assertIs(decision.state, TaskState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
