"""Tests for the Step 5 cancellation manager (aura.runtime.cancellation).

Deterministic synchronization only: ``asyncio.Event`` gates instead of
sleeps. Classification tests run synchronously against registry records;
delegation tests use a spy supervisor to observe ordering exactly.
"""

import asyncio
import unittest

from aura.runtime.cancellation import CancellationManager, CancelOutcome
from aura.runtime.registry import TaskRegistry, TaskState, UnknownCallError
from aura.runtime.supervisor import TaskSupervisor

CALL = "call_001"


class SpySupervisor:
    """Duck-typed stand-in that records delegation order (no asyncio)."""

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry
        self.calls: list[str] = []
        self.states_at_delegation: list[TaskState] = []

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    def cancel(self, call_id: str) -> bool:
        self.calls.append(call_id)
        self.states_at_delegation.append(self._registry.get(call_id).state)
        return True


class CancelOutcomeTests(unittest.TestCase):
    def test_outcomes_cover_the_required_cases(self):
        self.assertEqual(
            set(CancelOutcome.__members__),
            {"REQUESTED", "ALREADY_REQUESTED", "ALREADY_CANCELLED", "ALREADY_COMPLETED"},
        )

    def test_outcome_is_string_enum(self):
        self.assertIsInstance(CancelOutcome.REQUESTED, str)
        self.assertEqual(CancelOutcome.REQUESTED.value, "requested")


class ClassificationTests(unittest.TestCase):
    """All four outcome classes, synchronously (no asyncio task required)."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)
        self.manager = CancellationManager(self.supervisor)

    def _register(self, call_id: str = CALL) -> None:
        self.registry.register(
            call_id=call_id, tool_name="flight_search", spawn_version=1
        )

    def test_unknown_call_id_raises(self):
        with self.assertRaises(UnknownCallError):
            self.manager.request("ghost")
        self.assertEqual(len(self.registry), 0)  # validation changed nothing

    def test_unknown_call_does_not_reach_supervisor(self):
        # A spy proves no delegation happens for unknown calls.
        spy = SpySupervisor(self.registry)
        manager = CancellationManager(spy)
        with self.assertRaises(UnknownCallError):
            manager.request("ghost")
        self.assertEqual(spy.calls, [])

    def test_running_call_is_requested(self):
        self._register()
        self.registry.mark_running(CALL)
        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)

    def test_created_call_is_requested(self):
        self._register()  # never started
        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        self.assertEqual(
            self.registry.get(CALL).state, TaskState.CANCEL_REQUESTED
        )

    def test_repeat_request_is_safe(self):
        self._register()
        self.registry.mark_running(CALL)
        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )
        self.assertEqual(len(self.registry), 1)

    def test_completed_call_reports_already_completed(self):
        self._register()
        self.registry.mark_running(CALL)
        self.registry.mark_succeeded(CALL)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_COMPLETED
        )
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)  # untouched
        self.assertFalse(record.cancel_requested)  # never claimed a cancel

    def test_failed_call_reports_already_completed(self):
        self._register()
        self.registry.mark_running(CALL)
        self.registry.mark_failed(CALL)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_COMPLETED
        )
        self.assertEqual(self.registry.get(CALL).state, TaskState.FAILED)

    def test_cancelled_call_reports_already_cancelled(self):
        self._register()
        self.registry.mark_running(CALL)
        self.registry.request_cancel(CALL)
        self.registry.mark_cancelled(CALL)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_CANCELLED
        )
        self.assertEqual(self.registry.get(CALL).state, TaskState.CANCELLED)

    def test_record_is_preserved_after_request(self):
        self._register()
        self.registry.mark_running(CALL)
        self.manager.request(CALL)

        self.assertEqual(len(self.registry), 1)
        record = self.registry.get(CALL)
        self.assertEqual(record.call_id, CALL)
        self.assertEqual(record.spawn_version, 1)
        self.assertEqual(record.tool_name, "flight_search")
        self.assertIsNotNone(record.started_at)
        self.assertIsNone(record.finished_at)  # request is not a settlement

    def test_registry_only_record_without_asyncio_task_is_safe(self):
        # Intent is recorded even when nothing backs the record asynchronously.
        self._register()
        self.registry.mark_running(CALL)
        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        self.assertEqual(
            self.registry.get(CALL).state, TaskState.CANCEL_REQUESTED
        )


class DelegationTests(unittest.TestCase):
    """The manager delegates to the supervisor and never touches tasks itself."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.registry.register(
            call_id=CALL, tool_name="flight_search", spawn_version=1
        )
        self.registry.mark_running(CALL)
        self.spy = SpySupervisor(self.registry)
        self.manager = CancellationManager(self.spy)

    def test_intent_is_recorded_before_delegation(self):
        outcome = self.manager.request(CALL)

        self.assertEqual(outcome, CancelOutcome.REQUESTED)
        self.assertEqual(self.spy.calls, [CALL])  # delegated exactly once
        # Tombstone-first: the supervisor already saw CANCEL_REQUESTED.
        self.assertEqual(self.spy.states_at_delegation, [TaskState.CANCEL_REQUESTED])

    def test_repeat_request_re_delegates_safely(self):
        self.manager.request(CALL)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )
        self.assertEqual(self.spy.calls, [CALL, CALL])
        self.assertEqual(
            self.spy.states_at_delegation,
            [TaskState.CANCEL_REQUESTED, TaskState.CANCEL_REQUESTED],
        )

    def test_terminal_record_is_not_delegated(self):
        self.registry.mark_succeeded(CALL)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_COMPLETED
        )
        self.assertEqual(self.spy.calls, [])  # nothing to signal

    def test_manager_exposes_supervisor_and_shared_registry(self):
        self.assertIs(self.manager.registry, self.registry)
        self.assertIs(self.manager.supervisor, self.spy)


class LiveTaskCancellationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through a real TaskSupervisor and real asyncio tasks."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)
        self.manager = CancellationManager(self.supervisor)

    def _start(self, coro_factory, call_id: str = CALL) -> asyncio.Task:
        return self.supervisor.start(
            call_id, coro_factory, tool_name="flight_search", spawn_version=1
        )

    async def _start_blocked(self, call_id: str = CALL):
        started = asyncio.Event()
        gate = asyncio.Event()

        async def work():
            started.set()
            await gate.wait()

        task = self._start(work, call_id)
        await asyncio.wait_for(started.wait(), timeout=2)
        return task

    async def test_cancellation_of_running_task(self):
        task = await self._start_blocked()

        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        self.assertTrue(task.cancelled())
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)
        self.assertIsNotNone(record.finished_at)

    async def test_cancellation_request_reaches_registry_immediately(self):
        task = await self._start_blocked()

        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        # No await between request and this check: the tombstone is in place
        # before the task has had any chance to settle.
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)
        self.assertFalse(task.done())

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    async def test_repeated_cancellation_is_safe(self):
        task = await self._start_blocked()

        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_CANCELLED
        )
        self.assertEqual(self.registry.get(CALL).state, TaskState.CANCELLED)

    async def test_completed_task_cancellation_behavior(self):
        async def ok():
            return "value"

        task = self._start(ok)
        await asyncio.wait_for(task, timeout=2)

        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_COMPLETED
        )
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)  # unchanged
        self.assertFalse(record.cancel_requested)
        self.assertEqual(self.supervisor.result(CALL), "value")

    async def test_cancellation_does_not_delete_registry_record(self):
        task = await self._start_blocked()
        self.manager.request(CALL)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(len(self.registry), 1)
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertEqual(record.spawn_version, 1)  # stale-result fields intact
        self.assertIs(self.supervisor.task_for(CALL), task)

    async def test_task_ignoring_cancellation_stays_safe(self):
        started = asyncio.Event()
        gate = asyncio.Event()

        async def stubborn():
            started.set()
            try:
                await gate.wait()
            except asyncio.CancelledError:
                pass  # tool ignores the advisory cancellation
            return "late-result"

        task = self._start(stubborn)
        await asyncio.wait_for(started.wait(), timeout=2)

        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        value = await asyncio.wait_for(task, timeout=2)  # does not terminate it

        self.assertEqual(value, "late-result")
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)  # truth wins
        self.assertTrue(record.cancel_requested)  # tombstone kept for the gate
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_COMPLETED
        )
        self.assertEqual(len(self.registry), 1)

    async def test_delayed_cancellation_stays_pending_until_cleanup(self):
        started = asyncio.Event()
        gate = asyncio.Event()
        entered_cleanup = asyncio.Event()
        cleanup_gate = asyncio.Event()

        async def slow():
            started.set()
            try:
                await gate.wait()
            except asyncio.CancelledError:
                entered_cleanup.set()
                await cleanup_gate.wait()  # delays termination
                raise

        task = self._start(slow)
        await asyncio.wait_for(started.wait(), timeout=2)

        self.assertEqual(self.manager.request(CALL), CancelOutcome.REQUESTED)
        await asyncio.wait_for(entered_cleanup.wait(), timeout=2)

        # Cancellation is being processed but has NOT terminated the task.
        self.assertFalse(task.done())
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)

        # Repeating while pending is still safe.
        self.assertEqual(
            self.manager.request(CALL), CancelOutcome.ALREADY_REQUESTED
        )

        cleanup_gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual(self.registry.get(CALL).state, TaskState.CANCELLED)
        self.assertEqual(len(self.registry), 1)  # record retained


if __name__ == "__main__":
    unittest.main()
