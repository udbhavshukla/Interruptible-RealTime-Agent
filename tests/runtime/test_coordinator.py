"""Step 8 tests: runtime Coordinator.

Covers the four invariants:

- I1 single writer     : only the Coordinator mutates session state.
- I2 total order       : events are consumed in mailbox sequence order.
- I3 version tagging   : calls are stamped with the version they spawn under.
- I4 cancellation adv. : cancellation is attempted; the gate rejects stale work.

The required T0-T12 interruption scenario lives in
``InterruptionScenarioTests.test_required_t0_t12_scenario``.

No arbitrary sleeps: every wait is an ``asyncio.Event`` gate or an
``asyncio.wait_for(..., timeout)`` guard.
"""

import asyncio
import contextlib
import unittest

async def wait_started(tool, call_id):
    while call_id not in tool.started:
        await asyncio.sleep(0)
    await tool.started[call_id].wait()

from aura.runtime.acceptance import Decision
from aura.runtime.coordinator import Coordinator
from aura.runtime.events import Event, EventType
from aura.runtime.mailbox import SessionMismatchError
from aura.runtime.registry import TaskState

SESSION = "sess-1"
TOOL_TIMEOUT = 2.0


def user_event(event_type, intent=None, **payload):
    if intent is not None:
        payload["intent"] = intent
    return Event(session_id=SESSION, event_type=event_type, payload=payload, actor="user")


class ControlledTool:
    """Injectable async tool with deterministic per-call gates.

    Each call records ``(call_id, intent, spawn_version)``, exposes a
    ``started`` event (set when the tool body begins) and blocks on a
    ``release`` event. By default cancellation propagates (tool respects
    advisory cancel); calls listed in ``ignores_cancel`` swallow the
    cancellation and return a *late* result instead.
    """

    def __init__(self):
        self.calls = []
        self.started = {}
        self.release = {}
        self.ignores_cancel = set()

    async def __call__(self, *, intent, call_id, state):
        started = asyncio.Event()
        released = asyncio.Event()
        self.started[call_id] = started
        self.release[call_id] = released
        self.calls.append((call_id, intent, state.version))
        started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            if call_id not in self.ignores_cancel:
                raise
        return f"result:{intent}"


class CoordinatorTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared helpers for driving a Coordinator deterministically."""

    def make_coordinator(self, tool=None):
        tool = tool or ControlledTool()
        return Coordinator(SESSION, tool=tool), tool

    async def process(self, coordinator, event):
        await coordinator.mailbox.put(event)
        await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)

    async def settle_outcome(self, coordinator, call_id):
        """Wait for the supervised task, then process its outcome event.

        A cancelled call raises ``CancelledError`` at the await site — the
        outcome still reaches the mailbox through the supervisor watcher.
        """
        try:
            await asyncio.wait_for(coordinator.supervisor.task_for(call_id), TOOL_TIMEOUT)
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)

    async def cleanup_active(self, coordinator):
        """Cancel and gather every still-active call (deterministic teardown)."""
        tasks = []
        for call_id in list(coordinator.active_call_ids):
            coordinator.supervisor.cancel(call_id)
            tasks.append(coordinator.supervisor.task_for(call_id))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def emitted_types(self, coordinator):
        return [event.event_type for event in coordinator.emitted]


class CoordinatorConstructionTests(unittest.IsolatedAsyncioTestCase):
    def test_starts_empty_and_wired(self):
        coordinator = Coordinator(SESSION, tool=ControlledTool())
        self.assertEqual(coordinator.session_id, SESSION)
        self.assertEqual(coordinator.current_version, 0)
        self.assertIsNone(coordinator.state)
        self.assertEqual(coordinator.active_call_ids, ())
        self.assertEqual(coordinator.emitted, ())
        self.assertEqual(coordinator.mailbox.session_id, SESSION)
        self.assertIsNotNone(coordinator.registry)
        self.assertIsNotNone(coordinator.supervisor)
        self.assertIsNotNone(coordinator.gate)

    def test_rejects_bad_session_id(self):
        with self.assertRaises(ValueError):
            Coordinator("", tool=ControlledTool())
        with self.assertRaises(ValueError):
            Coordinator(None, tool=ControlledTool())

    def test_rejects_non_callable_tool(self):
        with self.assertRaises(TypeError):
            Coordinator(SESSION, tool="not-callable")

    async def test_mailbox_enforces_session_isolation(self):
        coordinator = Coordinator(SESSION, tool=ControlledTool())
        with self.assertRaises(SessionMismatchError):
            await coordinator.mailbox.put(
                Event(session_id="other", event_type=EventType.USER_INPUT, payload={})
            )


class UserInputTests(CoordinatorTestCase):
    async def test_first_input_creates_v1_and_call_001(self):
        coordinator, tool = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )

        self.assertEqual(coordinator.current_version, 1)
        record = coordinator.registry.get("call_001")
        self.assertEqual(record.spawn_version, 1)
        self.assertEqual(record.state, TaskState.RUNNING)
        self.assertIn("call_001", coordinator.active_call_ids)

        await asyncio.wait_for(wait_started(tool, "call_001"), TOOL_TIMEOUT)
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)
        self.assertEqual(
            self.emitted_types(coordinator),
            [EventType.STATE_VERSION_CHANGED, EventType.TASK_STARTED],
        )

        tool.release["call_001"].set()
        await self.settle_outcome(coordinator, "call_001")

        self.assertIn("call_001", coordinator.accepted_results)
        self.assertEqual(coordinator.accepted_results["call_001"],
                         "result:Find flight Bangalore to Delhi")
        self.assertEqual(coordinator.active_call_ids, ())
        self.assertEqual(coordinator.current_version, 1)

    async def test_second_input_with_new_intent_advances_version(self):
        coordinator, tool = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await asyncio.wait_for(wait_started(tool, "call_001"), TOOL_TIMEOUT)
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)
        await self.process(coordinator, user_event(EventType.USER_INPUT, "Find flight to Mumbai"))

        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.state.intent, "Find flight to Mumbai")
        self.assertEqual(coordinator.registry.get("call_002").spawn_version, 2)
        # supersession cancels the obsolete v1 call (advisory)
        self.assertTrue(coordinator.registry.get("call_001").cancel_requested)
        self.assertIn(EventType.TASK_CANCEL_REQUESTED, self.emitted_types(coordinator))

        await self.cleanup_active(coordinator)

    async def test_repeated_same_input_is_noop(self):
        coordinator, tool = self.make_coordinator()
        await self.process(coordinator, user_event(EventType.USER_INPUT, "Find flight to Delhi"))
        await asyncio.wait_for(wait_started(tool, "call_001"), TOOL_TIMEOUT)
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)
        await self.process(coordinator, user_event(EventType.USER_INPUT, "Find flight to Delhi"))

        self.assertEqual(coordinator.current_version, 1)
        self.assertEqual(coordinator.active_call_ids, ("call_001",))
        self.assertEqual(
            self.emitted_types(coordinator),
            [EventType.STATE_VERSION_CHANGED, EventType.TASK_STARTED],
        )
        await self.cleanup_active(coordinator)

    async def test_input_without_intent_emits_runtime_error(self):
        coordinator, _ = self.make_coordinator()
        await self.process(coordinator, user_event(EventType.USER_INPUT))

        self.assertEqual(coordinator.current_version, 0)
        self.assertEqual(self.emitted_types(coordinator), [EventType.RUNTIME_ERROR])
        self.assertIn("intent", coordinator.emitted[0].payload["error"])


class OrderingTests(CoordinatorTestCase):
    async def test_events_handled_in_mailbox_seq_order(self):
        """I2: consumption follows seq order and versions advance 1 -> 2 -> 3."""
        coordinator, tool = self.make_coordinator()
        await coordinator.mailbox.put(
            user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await coordinator.mailbox.put(user_event(EventType.USER_INTERRUPT, "Actually Mumbai"))
        await coordinator.mailbox.put(user_event(EventType.USER_INPUT, "By train instead"))

        first = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(coordinator.current_version, 1)
        second = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(coordinator.current_version, 2)
        third = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(coordinator.current_version, 3)

        self.assertLess(first.seq, second.seq)
        self.assertLess(second.seq, third.seq)

        transitions = [
            (event.payload["from"], event.payload["to"])
            for event in coordinator.emitted
            if event.event_type is EventType.STATE_VERSION_CHANGED
        ]
        self.assertEqual(transitions, [(0, 1), (1, 2), (2, 3)])
        
        # wait until every tool body actually began before reading its record
        for call_id in ("call_001", "call_002", "call_003"):
            await asyncio.wait_for(tool.started.setdefault(call_id, asyncio.Event()).wait(), TOOL_TIMEOUT)
        self.assertEqual(
            [record.spawn_version for record in coordinator.registry.records()],
            [1, 2, 3],
        )

        await self.cleanup_active(coordinator)

    async def test_unhandled_event_type_emits_runtime_error(self):
        coordinator, _ = self.make_coordinator()
        await self.process(coordinator, user_event(EventType.TASK_STARTED, call_id="call_999"))

        self.assertEqual(self.emitted_types(coordinator), [EventType.RUNTIME_ERROR])
        self.assertIn("unhandled", coordinator.emitted[0].payload["error"])


class InterruptionScenarioTests(CoordinatorTestCase):
    """The central demo scenario, deterministic end to end."""

    async def test_required_t0_t12_scenario(self):
        tool = ControlledTool()
        tool.ignores_cancel.add("call_001")  # late-returning obsolete task
        coordinator = Coordinator(SESSION, tool=tool)

        # --- T0-T2: initial input -------------------------------------
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        self.assertEqual(coordinator.current_version, 1)
        record_1 = coordinator.registry.get("call_001")
        self.assertEqual(record_1.spawn_version, 1)
        self.assertEqual(record_1.state, TaskState.RUNNING)
        await asyncio.wait_for(
            wait_started(tool, "call_001"),
            TOOL_TIMEOUT,
        )
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)

        # --- T3-T8: interruption --------------------------------------
        await self.process(coordinator, user_event(EventType.USER_INTERRUPT, "Actually Mumbai"))

        # 2. advance state to version 2
        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.state.intent, "Actually Mumbai")
        # 3. cancellation of call_001 requested (advisory, tombstone-first)
        self.assertTrue(coordinator.registry.get("call_001").cancel_requested)
        # 4. future work belongs to version 2 / 5. call_002 started
        record_2 = coordinator.registry.get("call_002")
        self.assertEqual(record_2.spawn_version, 2)
        await asyncio.wait_for(
            wait_started(tool, "call_002"),
            TOOL_TIMEOUT,
        )
        await asyncio.wait_for(tool.started.setdefault("call_002", asyncio.Event()).wait(), TOOL_TIMEOUT)

        # clear runtime events for the transitions
        transitions = [
            event
            for event in coordinator.emitted
            if event.event_type is EventType.STATE_VERSION_CHANGED
        ]
        self.assertEqual(len(transitions), 2)  # 0->1 then 1->2
        self.assertEqual(
            (transitions[1].payload["from"], transitions[1].payload["to"]),
            (1, 2),
        )
        self.assertEqual(transitions[1].payload["reason"], "user_interrupt")
        cancels = [
            event
            for event in coordinator.emitted
            if event.event_type is EventType.TASK_CANCEL_REQUESTED
        ]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].payload["call_id"], "call_001")
        self.assertEqual(cancels[0].payload["outcome"], "requested")
        self.assertIn(
            EventType.TASK_STARTED,
            [event.event_type for event in coordinator.emitted if event.payload.get("call_id") == "call_002"],
        )

        # --- T9-T10: call_001 returns late -> must be STALE ------------
        late_value = await asyncio.wait_for(
            coordinator.supervisor.task_for("call_001"), TOOL_TIMEOUT
        )
        self.assertIn("Delhi", late_value)  # obsolete result materialised
        await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)

        decision = coordinator.gate.evaluate("call_001", coordinator.current_version)
        self.assertIs(decision.decision, Decision.STALE)
        rejections = [
            event
            for event in coordinator.emitted
            if event.event_type is EventType.TASK_RESULT_REJECTED
        ]
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["call_id"], "call_001")
        self.assertEqual(rejections[0].payload["decision"], "stale")
        self.assertNotIn("call_001", coordinator.accepted_results)
        # I1/I11: the stale result never mutated current state
        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.state.intent, "Actually Mumbai")

        # --- T11-T12: call_002 returns -> accepted, result is Mumbai ---
        tool.release["call_002"].set()
        new_value = await asyncio.wait_for(
            coordinator.supervisor.task_for("call_002"), TOOL_TIMEOUT
        )
        self.assertIn("Mumbai", new_value)
        await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)

        decision = coordinator.gate.evaluate("call_002", coordinator.current_version)
        self.assertIs(decision.decision, Decision.ACCEPTED)
        self.assertEqual(
            coordinator.accepted_results.get("call_002"), "result:Actually Mumbai"
        )
        self.assertEqual(coordinator.active_call_ids, ())
        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.state.intent, "Actually Mumbai")

    async def test_interrupt_without_new_intent_is_noop(self):
        coordinator, tool = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await asyncio.wait_for(wait_started(tool, "call_001"), TOOL_TIMEOUT)
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)
        emitted_before = len(coordinator.emitted)

        await self.process(coordinator, user_event(EventType.USER_INTERRUPT))

        self.assertEqual(coordinator.current_version, 1)
        self.assertFalse(coordinator.registry.get("call_001").cancel_requested)
        self.assertEqual(len(coordinator.emitted), emitted_before)
        await self.cleanup_active(coordinator)

    async def test_cancelled_task_outcome_is_rejected_not_accepted(self):
        """A task cancelled before it finishes still flows through the gate."""
        coordinator, tool = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await asyncio.wait_for(wait_started(tool, "call_001"), TOOL_TIMEOUT)
        await asyncio.wait_for(tool.started.setdefault("call_001", asyncio.Event()).wait(), TOOL_TIMEOUT)

        coordinator.supervisor.cancel("call_001")  # advisory cancel
        await self.settle_outcome(coordinator, "call_001")

        self.assertNotIn("call_001", coordinator.accepted_results)
        rejections = [
            event
            for event in coordinator.emitted
            if event.event_type is EventType.TASK_RESULT_REJECTED
        ]
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["decision"], "cancelled")
        self.assertEqual(coordinator.active_call_ids, ())


class TaskOutcomeTests(CoordinatorTestCase):
    async def test_outcome_without_call_id_emits_runtime_error(self):
        coordinator, _ = self.make_coordinator()
        await self.process(
            coordinator,
            Event(session_id=SESSION, event_type=EventType.TASK_COMPLETED, payload={}),
        )
        self.assertEqual(self.emitted_types(coordinator), [EventType.RUNTIME_ERROR])
        self.assertIn("call_id", coordinator.emitted[0].payload["error"])

    async def test_unknown_call_outcome_is_rejected(self):
        """A completion for a call the registry never saw -> gate says no."""
        coordinator, _ = self.make_coordinator()
        await self.process(
            coordinator,
            Event(
                session_id=SESSION,
                event_type=EventType.TASK_COMPLETED,
                payload={"call_id": "call_999"},
            ),
        )
        rejections = [
            event
            for event in coordinator.emitted
            if event.event_type is EventType.TASK_RESULT_REJECTED
        ]
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["decision"], "unknown_call")
        self.assertNotIn("call_999", coordinator.accepted_results)
        self.assertEqual(coordinator.current_version, 0)

    async def test_consumer_survives_invalid_input_then_keeps_working(self):
        """A rejected event never kills the consumer loop (I1/I2 safety)."""
        coordinator, tool = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, intent={"nested": "value"})
        )
        self.assertEqual(self.emitted_types(coordinator), [EventType.RUNTIME_ERROR])
        self.assertEqual(coordinator.current_version, 0)

        # the loop still works afterwards
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        self.assertEqual(coordinator.current_version, 1)
        await self.cleanup_active(coordinator)


class RunLoopTests(CoordinatorTestCase):
    async def test_run_consumes_events_until_cancelled(self):
        coordinator, tool = self.make_coordinator()
        run_task = asyncio.create_task(coordinator.run())
        try:
            await coordinator.mailbox.put(
                user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
            )
            await asyncio.wait_for(coordinator.mailbox.join(), TOOL_TIMEOUT)
            self.assertEqual(coordinator.current_version, 1)
            self.assertIn("call_001", coordinator.active_call_ids)
        finally:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
            await self.cleanup_active(coordinator)

    async def test_run_loop_survives_handler_error(self):
        coordinator, _ = self.make_coordinator()
        run_task = asyncio.create_task(coordinator.run())
        try:
            await coordinator.mailbox.put(user_event(EventType.USER_INPUT))  # no intent
            await coordinator.mailbox.put(
                user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
            )
            await asyncio.wait_for(coordinator.mailbox.join(), TOOL_TIMEOUT)
            self.assertEqual(
                self.emitted_types(coordinator),
                [EventType.RUNTIME_ERROR, EventType.STATE_VERSION_CHANGED,
                 EventType.TASK_STARTED],
            )
            self.assertEqual(coordinator.current_version, 1)
        finally:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
            await self.cleanup_active(coordinator)


class SingleConsumerGuardTests(CoordinatorTestCase):
    """I2 hardening: exactly one consumer may run ``process_next()`` at a time."""

    async def test_single_consumer_processes_normally(self):
        """Sequential consumption by one consumer stays the normal path."""
        coordinator, _ = self.make_coordinator()
        await self.process(
            coordinator, user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await self.process(
            coordinator, user_event(EventType.USER_INTERRUPT, "Actually Mumbai")
        )
        self.assertEqual(coordinator.current_version, 2)

        # The superseded call_001 settles cancelled; its outcome is the only
        # possible next event (call_002 still runs), so get() cannot pick
        # anything else — consume it, then the queue is fully drained.
        outcome = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(outcome.event_type, EventType.TASK_CANCELLED)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)
        await self.cleanup_active(coordinator)

    async def test_concurrent_second_consumer_is_rejected(self):
        coordinator, _ = self.make_coordinator()

        # The first consumer enters process_next() and blocks in mailbox.get()
        # (the mailbox is empty), still holding the single-consumer guard.
        first = asyncio.create_task(coordinator.process_next())
        await asyncio.sleep(0)  # deterministic single yield (ready-queue FIFO)
        self.assertFalse(first.done())  # provably waiting inside get()

        # A second, concurrent consumer is rejected without touching the mailbox.
        with self.assertRaises(RuntimeError) as caught:
            await coordinator.process_next()
        self.assertIn("active consumer", str(caught.exception))
        self.assertFalse(first.done())  # first still owns the guard

        # The rejection consumed nothing: the first consumer still works.
        await coordinator.mailbox.put(
            user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        event = await asyncio.wait_for(first, TOOL_TIMEOUT)
        self.assertEqual(event.event_type, EventType.USER_INPUT)
        self.assertEqual(coordinator.current_version, 1)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)
        await self.cleanup_active(coordinator)

    async def test_guard_released_after_processing_completes(self):
        coordinator, _ = self.make_coordinator()
        # One consumer processes an event to completion ...
        await coordinator.mailbox.put(
            user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)

        # ... and a *different* consumer task may take the next one.
        await coordinator.mailbox.put(
            user_event(EventType.USER_INTERRUPT, "Actually Mumbai")
        )
        second = asyncio.create_task(coordinator.process_next())
        event = await asyncio.wait_for(second, TOOL_TIMEOUT)
        self.assertEqual(event.event_type, EventType.USER_INTERRUPT)
        self.assertEqual(coordinator.current_version, 2)

        # The superseded call_001's cancellation outcome arrives next —
        # consuming it proves the guard stays released for yet another step.
        outcome = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(outcome.event_type, EventType.TASK_CANCELLED)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)
        await self.cleanup_active(coordinator)

    async def test_guard_released_if_processing_raises(self):
        """Cancellation is the exception that escapes process_next()
        (handler errors are contained as RUNTIME_ERROR events); the guard
        must be released on that path too."""
        coordinator, _ = self.make_coordinator()

        first = asyncio.create_task(coordinator.process_next())
        await asyncio.sleep(0)  # deterministic single yield (ready-queue FIFO)
        self.assertFalse(first.done())

        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

        # The guard was released despite the exception: a new consumer works.
        await coordinator.mailbox.put(
            user_event(EventType.USER_INPUT, "Find flight Bangalore to Delhi")
        )
        event = await asyncio.wait_for(coordinator.process_next(), TOOL_TIMEOUT)
        self.assertEqual(event.event_type, EventType.USER_INPUT)
        self.assertEqual(coordinator.current_version, 1)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)
        await self.cleanup_active(coordinator)


if __name__ == "__main__":
    unittest.main()
