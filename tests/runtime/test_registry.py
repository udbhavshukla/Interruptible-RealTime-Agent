"""Tests for the Step 3 task registry (aura.runtime.registry)."""

import time
import unittest

from aura.runtime.registry import (
    DuplicateCallError,
    InvalidTransition,
    TaskRecord,
    TaskRegistry,
    TaskState,
    UnknownCallError,
)

CALL = "call_001"


def register(registry: TaskRegistry, call_id: str = CALL, **kwargs) -> TaskRecord:
    kwargs.setdefault("tool_name", "flight_search")
    kwargs.setdefault("spawn_version", 1)
    return registry.register(call_id=call_id, **kwargs)


class TaskStateTests(unittest.TestCase):
    REQUIRED = {
        "CREATED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "SUCCEEDED",
        "FAILED",
    }

    def test_required_states_exist(self):
        self.assertTrue(self.REQUIRED.issubset(set(TaskState.__members__)))

    def test_terminal_states(self):
        for state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
            self.assertTrue(state.is_terminal)
        for state in (TaskState.CREATED, TaskState.RUNNING, TaskState.CANCEL_REQUESTED):
            self.assertFalse(state.is_terminal)

    def test_state_is_string_enum(self):
        self.assertIsInstance(TaskState.RUNNING, str)
        self.assertEqual(TaskState.RUNNING.value, "running")


class RegistrationTests(unittest.TestCase):
    def test_successful_registration(self):
        registry = TaskRegistry()
        record = register(registry, plan_id="plan_1", step_id="step_2")

        self.assertIsInstance(record, TaskRecord)
        self.assertEqual(record.call_id, CALL)
        self.assertEqual(record.tool_name, "flight_search")
        self.assertEqual(record.state, TaskState.CREATED)
        self.assertFalse(record.cancel_requested)
        self.assertIsNone(record.started_at)
        self.assertIsNone(record.finished_at)
        self.assertEqual(len(registry), 1)

    def test_duplicate_call_id_rejected(self):
        registry = TaskRegistry()
        register(registry)
        with self.assertRaises(DuplicateCallError):
            register(registry)
        self.assertEqual(len(registry), 1)  # duplicate did not clobber

    def test_invalid_registration_arguments(self):
        registry = TaskRegistry()
        with self.assertRaises(ValueError):
            register(registry, call_id="")
        with self.assertRaises(ValueError):
            register(registry, tool_name="")
        with self.assertRaises(ValueError):
            register(registry, spawn_version=-1)
        with self.assertRaises(ValueError):
            register(registry, plan_id="")
        self.assertEqual(len(registry), 0)

    def test_lookup(self):
        registry = TaskRegistry()
        register(registry)
        record = registry.get(CALL)
        self.assertEqual(record.call_id, CALL)
        self.assertIn(CALL, registry)
        self.assertNotIn("nope", registry)
        self.assertEqual(len(registry), 1)
        self.assertEqual([r.call_id for r in registry.records()], [CALL])

    def test_unknown_call_id_handling(self):
        registry = TaskRegistry()
        with self.assertRaises(UnknownCallError):
            registry.get("missing")
        with self.assertRaises(UnknownCallError):
            registry.mark_running("missing")
        with self.assertRaises(UnknownCallError):
            registry.request_cancel("missing")
        with self.assertRaises(UnknownCallError):
            registry.mark_succeeded("missing")
        with self.assertRaises(UnknownCallError):
            registry.mark_failed("missing")
        with self.assertRaises(UnknownCallError):
            registry.mark_cancelled("missing")


class TimestampTests(unittest.TestCase):
    def test_timestamps_default_to_wall_clock(self):
        registry = TaskRegistry()
        before = time.time()
        record = register(registry)
        registry.mark_running(CALL)
        registry.mark_succeeded(CALL)
        after = time.time()

        self.assertLessEqual(before, record.created_at)
        self.assertLessEqual(record.created_at, record.started_at)
        self.assertLessEqual(record.started_at, record.finished_at)
        self.assertLessEqual(record.finished_at, after)

    def test_timestamps_are_populated_by_transition(self):
        registry = TaskRegistry()
        record = register(registry, at=100.0)
        self.assertEqual(record.created_at, 100.0)
        self.assertIsNone(record.started_at)
        self.assertIsNone(record.finished_at)

        registry.mark_running(CALL, at=105.0)
        self.assertEqual(record.started_at, 105.0)
        self.assertIsNone(record.finished_at)  # not settled yet

        registry.mark_succeeded(CALL, at=110.0)
        self.assertEqual(record.finished_at, 110.0)
        self.assertEqual(record.started_at, 105.0)  # unchanged


class LifecycleTests(unittest.TestCase):
    def test_created_to_running_to_succeeded(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL, at=1.0)
        self.assertEqual(record.state, TaskState.RUNNING)
        self.assertEqual(record.started_at, 1.0)
        registry.mark_succeeded(CALL, at=2.0)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertEqual(record.finished_at, 2.0)

    def test_created_to_running_to_failed(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL)
        registry.mark_failed(CALL, at=5.0)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertEqual(record.finished_at, 5.0)

    def test_cancellation_request(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL, at=1.0)

        registry.request_cancel(CALL, at=2.0)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)
        self.assertIsNone(record.finished_at)  # request is not a settlement

        registry.mark_cancelled(CALL, at=3.0)
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)
        self.assertEqual(record.finished_at, 3.0)

    def test_cancel_requested_from_created(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.request_cancel(CALL)
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)
        registry.mark_cancelled(CALL)
        self.assertEqual(record.state, TaskState.CANCELLED)

    def test_idempotent_cancellation_request(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL, at=1.0)

        registry.request_cancel(CALL, at=2.0)
        first_finished = record.finished_at
        registry.request_cancel(CALL, at=99.0)  # repeat -> no-op, no error
        registry.request_cancel(CALL, at=100.0)

        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)
        self.assertEqual(record.finished_at, first_finished)

        registry.mark_cancelled(CALL, at=3.0)
        registry.request_cancel(CALL, at=4.0)  # already terminal -> no-op
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertEqual(record.finished_at, 3.0)  # not overwritten

    def test_cancel_request_after_success_is_noop(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL)
        registry.mark_succeeded(CALL, at=5.0)

        registry.request_cancel(CALL, at=6.0)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertFalse(record.cancel_requested)  # never actually requested
        self.assertEqual(record.finished_at, 5.0)

    def test_late_success_after_cancel_request_keeps_flag(self):
        # T9-style outcome: the tool ignored cancellation and succeeded.
        registry = TaskRegistry()
        record = register(registry, spawn_version=1)
        registry.mark_running(CALL)
        registry.request_cancel(CALL, at=10.0)
        registry.mark_succeeded(CALL, at=11.0)

        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertTrue(record.cancel_requested)  # preserved for the gate
        self.assertEqual(record.spawn_version, 1)

    def test_mark_running_is_idempotent(self):
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_running(CALL, at=1.0)
        registry.mark_running(CALL, at=99.0)  # no-op
        self.assertEqual(record.state, TaskState.RUNNING)
        self.assertEqual(record.started_at, 1.0)


class RecordRetentionTests(unittest.TestCase):
    def test_record_retained_after_completion(self):
        registry = TaskRegistry()
        register(registry, spawn_version=2, plan_id="plan_1", step_id="step_1")
        registry.mark_running(CALL)
        registry.mark_succeeded(CALL, at=7.0)

        self.assertEqual(len(registry), 1)
        record = registry.get(CALL)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertEqual(record.spawn_version, 2)
        self.assertEqual(record.plan_id, "plan_1")
        self.assertEqual(record.step_id, "step_1")
        self.assertIsNotNone(record.started_at)
        self.assertEqual(record.finished_at, 7.0)

    def test_record_retained_after_cancellation(self):
        registry = TaskRegistry()
        register(registry, spawn_version=1)
        registry.mark_running(CALL)
        registry.request_cancel(CALL)
        registry.mark_cancelled(CALL, at=4.0)

        self.assertEqual(len(registry), 1)
        record = registry.get(CALL)
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)
        self.assertEqual(record.spawn_version, 1)  # never rewritten
        self.assertEqual(record.finished_at, 4.0)

    def test_spawn_version_preserved_across_lifecycle(self):
        registry = TaskRegistry()
        record = register(registry, spawn_version=7)
        for transition in (
            lambda: registry.mark_running(CALL),
            lambda: registry.request_cancel(CALL),
            lambda: registry.mark_cancelled(CALL),
        ):
            transition()
            self.assertEqual(record.spawn_version, 7)


class InvalidTransitionTests(unittest.TestCase):
    def test_succeeded_from_created_is_rejected(self):
        registry = TaskRegistry()
        register(registry)
        with self.assertRaises(InvalidTransition):
            registry.mark_succeeded(CALL)

    def test_failed_from_created_is_allowed(self):
        # Spawn-time failure: the call was registered but never started.
        registry = TaskRegistry()
        record = register(registry)
        registry.mark_failed(CALL, at=3.0)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertEqual(record.finished_at, 3.0)

    def test_mark_running_from_cancel_requested_is_rejected(self):
        registry = TaskRegistry()
        register(registry)
        registry.request_cancel(CALL)
        with self.assertRaises(InvalidTransition):
            registry.mark_running(CALL)

    def test_transitions_on_terminal_states_are_rejected(self):
        registry = TaskRegistry()
        register(registry)
        registry.mark_running(CALL)
        registry.mark_succeeded(CALL)

        for attempt in (
            lambda: registry.mark_running(CALL),
            lambda: registry.mark_succeeded(CALL),
            lambda: registry.mark_failed(CALL),
            lambda: registry.mark_cancelled(CALL),
        ):
            with self.assertRaises(InvalidTransition):
                attempt()

        self.assertEqual(registry.get(CALL).state, TaskState.SUCCEEDED)

    def test_invalid_transition_leaves_record_untouched(self):
        registry = TaskRegistry()
        register(registry)  # stays CREATED
        with self.assertRaises(InvalidTransition):
            registry.mark_succeeded(CALL, at=50.0)
        record = registry.get(CALL)
        self.assertEqual(record.state, TaskState.CREATED)
        self.assertIsNone(record.finished_at)


if __name__ == "__main__":
    unittest.main()
