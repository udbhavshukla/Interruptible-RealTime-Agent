"""Tests for the Step 4 task supervisor (aura.runtime.supervisor).

Deterministic synchronization only: ``asyncio.Event`` gates instead of
sleeps; awaiting the supervised task itself is the completion signal. No test
leaves a pending task behind.
"""

import asyncio
import unittest

from aura.runtime.registry import (
    DuplicateCallError,
    TaskRecord,
    TaskRegistry,
    TaskState,
    UnknownCallError,
)
from aura.runtime.supervisor import TaskSupervisor

CALL = "call_001"


def start(
    supervisor: TaskSupervisor,
    coro_factory,
    call_id: str = CALL,
    **kwargs,
) -> asyncio.Task:
    kwargs.setdefault("tool_name", "flight_search")
    kwargs.setdefault("spawn_version", 1)
    return supervisor.start(call_id, coro_factory, **kwargs)


class SupervisorValidationTests(unittest.TestCase):
    """Sync checks: validation must fail before anything is registered."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)

    def test_supervisor_uses_injected_registry(self):
        self.assertIs(self.supervisor.registry, self.registry)

    def test_start_requires_running_loop(self):
        with self.assertRaises(RuntimeError):
            start(self.supervisor, lambda: asyncio.sleep(0))
        self.assertEqual(len(self.registry), 0)  # no partial registration

    def test_non_callable_factory_rejected(self):
        with self.assertRaises(TypeError):
            start(self.supervisor, 42)
        self.assertEqual(len(self.registry), 0)


class SupervisorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)

    async def test_starting_a_task(self):
        started = asyncio.Event()
        gate = asyncio.Event()

        async def work():
            started.set()
            await gate.wait()

        task = start(self.supervisor, work)

        self.assertIsInstance(task, asyncio.Task)
        self.assertIs(self.supervisor.task_for(CALL), task)
        self.assertFalse(task.done())
        self.assertEqual(self.registry.get(CALL).state, TaskState.RUNNING)

        await asyncio.wait_for(started.wait(), timeout=2)  # execution began
        gate.set()
        await asyncio.wait_for(task, timeout=2)
        self.assertEqual(self.registry.get(CALL).state, TaskState.SUCCEEDED)

    async def test_task_reaches_succeeded(self):
        async def work():
            return 42

        task = start(self.supervisor, work)
        wrapper_result = await asyncio.wait_for(task, timeout=2)

        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())
        self.assertIsNone(task.exception())
        self.assertEqual(wrapper_result, 42)  # awaited task returns the value
        self.assertEqual(self.supervisor.result(CALL), 42)  # and it is retained
        self.assertIsNone(self.supervisor.error(CALL))
        self.assertIsNotNone(record.finished_at)

    async def test_task_failure_reaches_failed(self):
        async def boom():
            raise ValueError("tool exploded")

        task = start(self.supervisor, boom)
        await asyncio.wait_for(task, timeout=2)  # failure must not raise here

        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertTrue(task.done())
        self.assertIsNone(task.exception())  # swallowed: nothing unretrieved
        error = self.supervisor.error(CALL)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "tool exploded")
        self.assertIsNotNone(record.finished_at)

    async def test_failure_does_not_crash_supervisor(self):
        async def boom():
            raise RuntimeError("bad tool")

        await asyncio.wait_for(start(self.supervisor, boom), timeout=2)
        self.assertEqual(self.registry.get(CALL).state, TaskState.FAILED)

        async def fine():
            return "ok"

        task = start(self.supervisor, fine, call_id="call_002")
        await asyncio.wait_for(task, timeout=2)
        self.assertEqual(self.registry.get("call_002").state, TaskState.SUCCEEDED)
        self.assertEqual(len(self.registry), 2)

    async def test_non_awaitable_factory_fails_the_task(self):
        task = start(self.supervisor, lambda: "not awaitable")
        await asyncio.wait_for(task, timeout=2)
        self.assertEqual(self.registry.get(CALL).state, TaskState.FAILED)
        self.assertIsInstance(self.supervisor.error(CALL), TypeError)

    async def test_sync_raising_factory_fails_the_task(self):
        def bad():
            raise OSError("spawn failed")

        task = start(self.supervisor, bad)
        await asyncio.wait_for(task, timeout=2)
        self.assertEqual(self.registry.get(CALL).state, TaskState.FAILED)
        self.assertIsInstance(self.supervisor.error(CALL), OSError)

    async def test_duplicate_call_id_rejected_while_first_task_alive(self):
        gate = asyncio.Event()

        async def work():
            await gate.wait()

        first = start(self.supervisor, work)
        with self.assertRaises(DuplicateCallError):
            start(self.supervisor, work)
        self.assertIs(self.supervisor.task_for(CALL), first)  # original intact
        self.assertEqual(len(self.registry), 1)

        gate.set()
        await asyncio.wait_for(first, timeout=2)

    async def test_metadata_is_preserved(self):
        async def work():
            return None

        task = start(
            self.supervisor,
            work,
            spawn_version=3,
            plan_id="plan_9",
            step_id="step_4",
        )
        await asyncio.wait_for(task, timeout=2)

        record = self.registry.get(CALL)
        self.assertEqual(record.call_id, CALL)
        self.assertEqual(record.tool_name, "flight_search")
        self.assertEqual(record.spawn_version, 3)
        self.assertEqual(record.plan_id, "plan_9")
        self.assertEqual(record.step_id, "step_4")
        self.assertEqual(record.state, TaskState.SUCCEEDED)

        # Metadata and terminal state survive a late cancel attempt.
        self.assertFalse(self.supervisor.cancel(CALL))
        self.assertEqual(record.spawn_version, 3)
        self.assertEqual(record.state, TaskState.SUCCEEDED)


class SupervisorCancellationTests(unittest.IsolatedAsyncioTestCase):
    """Cancellation is advisory, idempotent, and tombstone-first."""

    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)

    async def _start_blocked(self, *, call_id=CALL, swallow=False, value=None):
        """Start a task blocked on an Event; return (task, gate) once running."""
        started = asyncio.Event()
        gate = asyncio.Event()

        async def work():
            started.set()
            try:
                await gate.wait()
            except asyncio.CancelledError:
                if not swallow:
                    raise
            return value

        task = start(self.supervisor, work, call_id=call_id)
        await asyncio.wait_for(started.wait(), timeout=2)
        return task, gate

    async def test_cancellation_of_running_task(self):
        task, _gate = await self._start_blocked()

        self.assertTrue(self.supervisor.cancel(CALL))
        record = self.registry.get(CALL)
        # Tombstone recorded synchronously, before the task settles.
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        self.assertTrue(task.cancelled())
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)
        self.assertIsNotNone(record.finished_at)

    async def test_repeated_cancellation_is_safe(self):
        task, _gate = await self._start_blocked()

        self.assertTrue(self.supervisor.cancel(CALL))
        self.assertTrue(self.supervisor.cancel(CALL))  # repeat while running
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        finished_at = record.finished_at
        self.assertFalse(self.supervisor.cancel(CALL))  # already terminal
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertEqual(record.finished_at, finished_at)  # not overwritten

    async def test_cancellation_is_advisory_and_ignorable(self):
        # A tool that swallows CancelledError and returns anyway.
        task, _gate = await self._start_blocked(swallow=True, value="late-result")

        self.assertTrue(self.supervisor.cancel(CALL))
        value = await asyncio.wait_for(task, timeout=2)

        self.assertEqual(value, "late-result")
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)  # outcome is the truth
        self.assertTrue(record.cancel_requested)  # tombstone kept for the gate
        self.assertEqual(self.supervisor.result(CALL), "late-result")
        self.assertFalse(self.supervisor.cancel(CALL))  # terminal -> no-op
        self.assertEqual(record.state, TaskState.SUCCEEDED)  # cancel cannot flip it

    async def test_cancel_before_first_execution(self):
        gate = asyncio.Event()
        task = start(self.supervisor, lambda: gate.wait(), call_id="call_pre")

        self.assertTrue(self.supervisor.cancel("call_pre"))
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        record = self.registry.get("call_pre")
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)

    async def test_unknown_call_id_handling(self):
        with self.assertRaises(UnknownCallError):
            self.supervisor.cancel("ghost")
        with self.assertRaises(UnknownCallError):
            self.supervisor.task_for("ghost")
        with self.assertRaises(UnknownCallError):
            self.supervisor.result("ghost")
        with self.assertRaises(UnknownCallError):
            self.supervisor.error("ghost")
        # Failed lookups created nothing.
        self.assertEqual(len(self.registry), 0)

    async def test_cancelled_task_remains_in_registry(self):
        task, _gate = await self._start_blocked()
        self.supervisor.cancel(CALL)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(len(self.registry), 1)
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCELLED)
        # Supervisor keeps the (cancelled) task too.
        self.assertIs(self.supervisor.task_for(CALL), task)


class RegistrySupervisorConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = TaskRegistry()
        self.supervisor = TaskSupervisor(self.registry)

    async def test_completed_tasks_remain_in_registry(self):
        async def ok():
            return "done"

        task = start(self.supervisor, ok)
        await asyncio.wait_for(task, timeout=2)

        self.assertEqual(len(self.registry), 1)
        record = self.registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertIs(self.supervisor.task_for(CALL), task)  # association kept

    async def test_registry_and_supervisor_stay_consistent(self):
        async def ok():
            return 1

        async def boom():
            raise KeyError("nope")

        t1 = start(self.supervisor, ok, call_id="call_ok")
        t2 = start(self.supervisor, boom, call_id="call_bad")
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)

        self.assertEqual(len(self.registry), 2)
        # Every registered call has a supervisor task, and vice versa.
        for call_id in ("call_ok", "call_bad"):
            self.assertIsInstance(self.registry.get(call_id), TaskRecord)
            self.assertIsNotNone(self.supervisor.task_for(call_id))

        self.assertEqual(self.registry.get("call_ok").state, TaskState.SUCCEEDED)
        self.assertEqual(self.registry.get("call_bad").state, TaskState.FAILED)
        self.assertEqual(self.supervisor.result("call_ok"), 1)
        self.assertIsInstance(self.supervisor.error("call_bad"), KeyError)

    async def test_states_never_leak_between_calls(self):
        async def ok():
            return "a"

        async def boom():
            raise RuntimeError("b")

        await asyncio.wait_for(start(self.supervisor, ok, call_id="a"), timeout=2)
        await asyncio.wait_for(start(self.supervisor, boom, call_id="b"), timeout=2)

        self.assertEqual(self.registry.get("a").state, TaskState.SUCCEEDED)
        self.assertEqual(self.registry.get("b").state, TaskState.FAILED)
        self.assertIsNone(self.supervisor.error("a"))
        self.assertIsNone(self.supervisor.result("b"))


if __name__ == "__main__":
    unittest.main()
