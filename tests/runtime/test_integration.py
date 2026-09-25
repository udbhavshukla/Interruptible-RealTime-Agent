"""Step 10: integration and failure tests for the AURA runtime.

Wires the whole runtime together — Coordinator + SessionMailbox +
TaskSupervisor + CancellationManager + AcceptanceGate — and drives it
through the scenarios that decide whether an interruptible agent is
trustworthy:

 1. normal task completion
 2. user interruption during execution
 3. old task returns after interruption
 4. cancellation succeeds
 5. cancellation arrives too late
 6. task ignores cancellation temporarily
 7. old result is still rejected
 8. new-version result is accepted
 9. multiple interruptions
10. rapid successive user corrections
11. task failure
12. unknown task result
13. duplicate cancellation
14. completed task receiving cancellation request
15. session version monotonicity
16. no stale result mutates current state

Race-condition policy
---------------------
- Assert *exact* terminal states only after awaiting the supervised task
  (the ``settle`` helper); intermediate assertions use monotonic facts
  only (``cancel_requested`` never flips back; versions only grow).
- Sequencing uses ``asyncio.Event`` gates (``started`` / ``release``) and
  supervised-task completion — no wall-clock sleeps. Every await is
  wrapped in ``wait_for`` purely as a hang guard.
- The burst test enqueues every user event *before* consuming any,
  proving later-enqueued task outcomes (higher ``seq``) can never
  preempt user input (mailbox FIFO within the DATA lane).
"""

import asyncio
import unittest

from aura.runtime.acceptance import Decision
from aura.runtime.cancellation import CancelOutcome, CancellationManager
from aura.runtime.coordinator import Coordinator
from aura.runtime.events import Event, EventType
from aura.runtime.registry import TaskState

TIMEOUT = 2.0
SESSION = "integration-session"

INTENT_A = "Find flight Bangalore to Delhi"
INTENT_B = "Actually Mumbai"
INTENT_C = "Actually Chennai"
INTENT_D = "Actually Pune"


class ScriptedTool:
    """Deterministic per-call scripted tool (no network, no timers).

    Behaviors (programmed per ``call_id`` before spawn):

    - ``normal`` (default): blocks on a per-call release gate; a
      cancellation delivered while blocked propagates — cancellation
      succeeds.
    - ``ignore``: swallows cancellation signals and finishes only when
      explicitly released — the "late return" / "ignores cancel" tool.
    - ``fail``: raises immediately — the failure path.
    """

    def __init__(self):
        self.behaviors = {}
        self.started = {}
        self.cancel_signals = {}  # per call: cancellation signals swallowed
        self._release = {}

    def program(self, call_id, behavior):
        self.behaviors[call_id] = behavior

    def release(self, call_id):
        """Pre-registered, idempotent: safe before or after the body runs."""
        self._release.setdefault(call_id, asyncio.Event()).set()

    def started_gate(self, call_id):
        return self.started.setdefault(call_id, asyncio.Event())

    async def __call__(self, *, intent, call_id, state):
        started = self.started.setdefault(call_id, asyncio.Event())
        released = self._release.setdefault(call_id, asyncio.Event())
        started.set()

        behavior = self.behaviors.get(call_id, "normal")
        if behavior == "fail":
            raise RuntimeError(f"scripted failure for {call_id}")

        if behavior == "ignore":
            while True:
                try:
                    await released.wait()
                    break
                except asyncio.CancelledError:
                    self.cancel_signals[call_id] = (
                        self.cancel_signals.get(call_id, 0) + 1
                    )
            return f"result:{intent}"

        await released.wait()
        return f"result:{intent}"


class IntegrationTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared harness: build a wired runtime, drive it, assert facts."""

    def make(self):
        tool = ScriptedTool()
        return Coordinator(SESSION, tool=tool, tool_name="scripted_tool"), tool

    def user_event(self, event_type, intent=None, **payload):
        if intent is not None:
            payload["intent"] = intent
        return Event(
            session_id=SESSION, event_type=event_type, payload=payload, actor="user"
        )

    async def consume(self, coordinator):
        return await asyncio.wait_for(coordinator.process_next(), TIMEOUT)

    async def say(self, coordinator, event_type, intent):
        """Enqueue a user event, then let the Coordinator consume it."""
        await asyncio.wait_for(
            coordinator.mailbox.put(self.user_event(event_type, intent)), TIMEOUT
        )
        return await self.consume(coordinator)

    async def wait_started(self, tool, call_id):
        await asyncio.wait_for(tool.started_gate(call_id).wait(), TIMEOUT)

    async def settle(self, coordinator, call_id):
        """Wait for the task to reach a terminal state, consume its outcome.

        Swallows ``CancelledError`` from awaiting a cancelled task — the
        registry has already settled it and the watcher still delivers the
        outcome event.
        """
        try:
            await asyncio.wait_for(
                coordinator.supervisor.task_for(call_id), TIMEOUT
            )
        except asyncio.CancelledError:
            pass
        return await self.consume(coordinator)

    def outbox(self, coordinator, event_type):
        return [e for e in coordinator.emitted if e.event_type is event_type]

    def transitions(self, coordinator):
        return [
            (e.payload["from"], e.payload["to"])
            for e in self.outbox(coordinator, EventType.STATE_VERSION_CHANGED)
        ]


class NormalCompletionTests(IntegrationTestCase):
    async def test_normal_completion_is_accepted(self):
        """(1) A task that finishes on its own is accepted at its version."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        record = coordinator.registry.get("call_001")
        self.assertEqual(record.state, TaskState.RUNNING)
        self.assertEqual(record.spawn_version, 1)
        self.assertFalse(record.cancel_requested)

        tool.release("call_001")
        outcome = await self.settle(coordinator, "call_001")

        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertFalse(record.cancel_requested)
        self.assertIs(
            coordinator.gate.evaluate("call_001", coordinator.current_version).decision,
            Decision.ACCEPTED,
        )
        self.assertEqual(
            coordinator.accepted_results["call_001"], f"result:{INTENT_A}"
        )
        self.assertEqual(coordinator.active_call_ids, ())
        self.assertEqual(
            self.outbox(coordinator, EventType.TASK_RESULT_REJECTED), []
        )
        self.assertEqual(coordinator.current_version, 1)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)


class InterruptionTests(IntegrationTestCase):
    async def test_interruption_during_execution(self):
        """(2) Interrupt mid-flight: v2, tombstone, cancel, new call at v2."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")

        # state advanced
        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.state.intent, INTENT_B)
        self.assertEqual(self.transitions(coordinator), [(0, 1), (1, 2)])
        # cancellation attempted on the old call (monotonic fact: stays True)
        old = coordinator.registry.get("call_001")
        self.assertTrue(old.cancel_requested)
        self.assertIsNot(old.state, TaskState.SUCCEEDED)
        cancels = self.outbox(coordinator, EventType.TASK_CANCEL_REQUESTED)
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].payload["call_id"], "call_001")
        # future work belongs to v2
        self.assertEqual(coordinator.registry.get("call_002").spawn_version, 2)
        self.assertIn("call_002", coordinator.active_call_ids)

        # both calls settle; neither outcome may spoil the state
        outcome_1 = await self.settle(coordinator, "call_001")
        self.assertEqual(outcome_1.event_type, EventType.TASK_CANCELLED)
        self.assertEqual(old.state, TaskState.CANCELLED)
        tool.release("call_002")
        outcome_2 = await self.settle(coordinator, "call_002")
        self.assertEqual(outcome_2.event_type, EventType.TASK_COMPLETED)

        self.assertNotIn("call_001", coordinator.accepted_results)
        self.assertEqual(
            coordinator.accepted_results["call_002"], f"result:{INTENT_B}"
        )
        self.assertEqual(coordinator.current_version, 2)
        self.assertEqual(coordinator.active_call_ids, ())

    async def test_old_task_returns_after_interruption(self):
        """(3) The old call ignores the cancel and returns late (T8)."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")

        # tombstone recorded while the task is still alive
        old = coordinator.registry.get("call_001")
        self.assertTrue(old.cancel_requested)
        self.assertFalse(coordinator.supervisor.task_for("call_001").done())

        # late return: the tool shrugged off the cancellation
        tool.release("call_001")
        outcome = await self.settle(coordinator, "call_001")

        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertEqual(old.state, TaskState.SUCCEEDED)  # it really returned
        self.assertTrue(old.cancel_requested)
        self.assertIn("Delhi", coordinator.supervisor.result("call_001"))

        tool.release("call_002")
        await self.settle(coordinator, "call_002")

    async def test_new_version_result_is_accepted(self):
        """(8) Only work spawned at the current version is accepted."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")

        # old one returns late -> rejected
        tool.release("call_001")
        await self.settle(coordinator, "call_001")
        # new one completes -> accepted at version 2
        tool.release("call_002")
        outcome = await self.settle(coordinator, "call_002")

        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertIs(
            coordinator.gate.evaluate("call_002", coordinator.current_version).decision,
            Decision.ACCEPTED,
        )
        self.assertEqual(
            coordinator.accepted_results.get("call_002"), f"result:{INTENT_B}"
        )
        self.assertNotIn("call_001", coordinator.accepted_results)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["call_id"], "call_001")
        self.assertEqual(coordinator.active_call_ids, ())
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)


class CancellationTests(IntegrationTestCase):
    async def test_cancellation_succeeds_and_is_rejected(self):
        """(4) A cooperative tool is cancelled; its outcome is not accepted."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        self.assertTrue(coordinator.supervisor.cancel("call_001"))
        outcome = await self.settle(coordinator, "call_001")

        record = coordinator.registry.get("call_001")
        self.assertEqual(outcome.event_type, EventType.TASK_CANCELLED)
        self.assertEqual(record.state, TaskState.CANCELLED)
        self.assertTrue(record.cancel_requested)
        self.assertIsNone(coordinator.supervisor.result("call_001"))
        # Same version, but the cancel flag alone forbids acceptance.
        self.assertIs(
            coordinator.gate.evaluate("call_001", coordinator.current_version).decision,
            Decision.CANCELLED,
        )
        self.assertNotIn("call_001", coordinator.accepted_results)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["decision"], "cancelled")
        self.assertEqual(coordinator.active_call_ids, ())

    async def test_cancellation_arrives_too_late(self):
        """(5) Completion already happened: the cancel signal is a no-op."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        tool.release("call_001")
        await asyncio.wait_for(coordinator.supervisor.task_for("call_001"), TIMEOUT)
        # task settled SUCCEEDED; now the cancel arrives — too late
        self.assertFalse(coordinator.supervisor.cancel("call_001"))

        record = coordinator.registry.get("call_001")
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertFalse(record.cancel_requested)  # late cancel leaves no mark

        outcome = await self.consume(coordinator)  # outcome was already queued
        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertIn("call_001", coordinator.accepted_results)
        self.assertEqual(
            self.outbox(coordinator, EventType.TASK_RESULT_REJECTED), []
        )

    async def test_task_ignores_cancellation_temporarily(self):
        """(6) A tool that shrugs off cancel and finishes is still rejected."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        self.assertTrue(coordinator.supervisor.cancel("call_001"))
        task = coordinator.supervisor.task_for("call_001")
        record = coordinator.registry.get("call_001")
        # deterministic: intent is on record, the task survived the signal
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertTrue(record.cancel_requested)
        self.assertFalse(task.done())

        # it keeps running "a while longer", then finishes normally
        tool.release("call_001")
        outcome = await self.settle(coordinator, "call_001")

        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertTrue(record.cancel_requested)
        self.assertGreaterEqual(tool.cancel_signals.get("call_001", 0), 1)
        # rejection without staleness: same version, cancel flag decides
        self.assertIs(
            coordinator.gate.evaluate("call_001", coordinator.current_version).decision,
            Decision.CANCELLED,
        )
        self.assertNotIn("call_001", coordinator.accepted_results)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(rejections[0].payload["decision"], "cancelled")

    async def test_duplicate_cancellation_is_idempotent(self):
        """(13) Repeat requests classify cleanly and never break the task."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")  # keeps the record observable
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        manager = CancellationManager(coordinator.supervisor)
        first = manager.request("call_001")
        second = manager.request("call_001")  # no await between: still alive
        third = manager.request("call_001")

        self.assertIs(first, CancelOutcome.REQUESTED)
        self.assertIs(second, CancelOutcome.ALREADY_REQUESTED)
        self.assertIs(third, CancelOutcome.ALREADY_REQUESTED)
        record = coordinator.registry.get("call_001")
        self.assertEqual(record.state, TaskState.CANCEL_REQUESTED)
        self.assertFalse(coordinator.supervisor.task_for("call_001").done())

        # the task still settles correctly afterwards
        tool.release("call_001")
        outcome = await self.settle(coordinator, "call_001")
        self.assertEqual(outcome.event_type, EventType.TASK_COMPLETED)
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertTrue(record.cancel_requested)
        self.assertGreaterEqual(tool.cancel_signals.get("call_001", 0), 1)
        self.assertNotIn("call_001", coordinator.accepted_results)

    async def test_cancellation_of_completed_task_is_noop(self):
        """(14) A completed task stays completed; the result is kept."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        tool.release("call_001")
        await asyncio.wait_for(coordinator.supervisor.task_for("call_001"), TIMEOUT)

        manager = CancellationManager(coordinator.supervisor)
        outcome = manager.request("call_001")
        self.assertIs(outcome, CancelOutcome.ALREADY_COMPLETED)

        record = coordinator.registry.get("call_001")
        self.assertEqual(record.state, TaskState.SUCCEEDED)
        self.assertFalse(record.cancel_requested)  # not retroactively tainted
        self.assertIsNotNone(coordinator.supervisor.result("call_001"))

        consumed = await self.consume(coordinator)
        self.assertEqual(consumed.event_type, EventType.TASK_COMPLETED)
        self.assertIs(
            coordinator.gate.evaluate("call_001", coordinator.current_version).decision,
            Decision.ACCEPTED,
        )
        self.assertIn("call_001", coordinator.accepted_results)


class FailureTests(IntegrationTestCase):
    async def test_task_failure_is_rejected_not_accepted(self):
        """(11) A raising tool fails the call without crashing the runtime."""
        coordinator, tool = self.make()
        tool.program("call_001", "fail")
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)

        outcome = await self.settle(coordinator, "call_001")

        record = coordinator.registry.get("call_001")
        self.assertEqual(outcome.event_type, EventType.TASK_FAILED)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertIsInstance(
            coordinator.supervisor.error("call_001"), RuntimeError
        )
        self.assertIs(
            coordinator.gate.evaluate("call_001", coordinator.current_version).decision,
            Decision.INVALID,  # there is no result to accept
        )
        self.assertNotIn("call_001", coordinator.accepted_results)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(rejections[0].payload["decision"], "invalid")
        self.assertEqual(coordinator.active_call_ids, ())

        # the consumer survived and keeps working
        await self.say(coordinator, EventType.USER_INPUT, INTENT_B)
        self.assertEqual(coordinator.current_version, 2)
        tool.release("call_002")
        await self.settle(coordinator, "call_002")
        self.assertIn("call_002", coordinator.accepted_results)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)

    async def test_unknown_task_result_is_rejected(self):
        """(12) An outcome for a call the runtime never spawned is refused."""
        coordinator, tool = self.make()
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")

        await asyncio.wait_for(
            coordinator.mailbox.put(
                Event(
                    session_id=SESSION,
                    event_type=EventType.TASK_COMPLETED,
                    payload={"call_id": "call_ghost", "spawn_version": 99},
                    actor="task:call_ghost",
                )
            ),
            TIMEOUT,
        )
        consumed = await self.consume(coordinator)

        self.assertEqual(consumed.event_type, EventType.TASK_COMPLETED)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["call_id"], "call_ghost")
        self.assertEqual(rejections[0].payload["decision"], "unknown_call")
        self.assertEqual(coordinator.accepted_results, {})
        # the real call is untouched and still tracked
        self.assertIn("call_001", coordinator.active_call_ids)
        self.assertEqual(coordinator.current_version, 1)

        tool.release("call_001")
        await self.settle(coordinator, "call_001")
        self.assertIn("call_001", coordinator.accepted_results)


class BurstTests(IntegrationTestCase):
    async def test_multiple_interruptions(self):
        """(9) Three corrections in a row: versions 1-2-3, last intent wins."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")
        tool.program("call_002", "ignore")

        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_C)
        await self.wait_started(tool, "call_003")

        self.assertEqual(coordinator.current_version, 3)
        self.assertEqual(coordinator.state.intent, INTENT_C)
        self.assertEqual(
            self.transitions(coordinator), [(0, 1), (1, 2), (2, 3)]
        )
        records = coordinator.registry
        self.assertEqual(records.get("call_001").spawn_version, 1)
        self.assertEqual(records.get("call_002").spawn_version, 2)
        self.assertEqual(records.get("call_003").spawn_version, 3)
        self.assertTrue(records.get("call_001").cancel_requested)
        self.assertTrue(records.get("call_002").cancel_requested)
        self.assertFalse(records.get("call_003").cancel_requested)

        # both obsolete calls return late -> both rejected as stale
        tool.release("call_001")
        tool.release("call_002")
        await asyncio.wait_for(coordinator.supervisor.task_for("call_001"), TIMEOUT)
        await asyncio.wait_for(coordinator.supervisor.task_for("call_002"), TIMEOUT)
        await self.settle(coordinator, "call_001")
        await self.settle(coordinator, "call_002")

        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(
            {e.payload["call_id"] for e in rejections}, {"call_001", "call_002"}
        )
        self.assertTrue(all(e.payload["decision"] == "stale" for e in rejections))

        # the current call still completes and is accepted at v3
        tool.release("call_003")
        await self.settle(coordinator, "call_003")
        self.assertEqual(
            coordinator.accepted_results.get("call_003"), f"result:{INTENT_C}"
        )
        self.assertEqual(set(coordinator.accepted_results), {"call_003"})
        self.assertEqual(coordinator.current_version, 3)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)

    async def test_rapid_successive_corrections(self):
        """(10) A burst enqueued before any processing: total order holds."""
        coordinator, tool = self.make()
        burst = [
            (EventType.USER_INPUT, INTENT_A),
            (EventType.USER_INTERRUPT, INTENT_B),
            (EventType.USER_INTERRUPT, INTENT_C),
            (EventType.USER_INTERRUPT, INTENT_D),
        ]
        # All user events enter the mailbox before a single consumption,
        # so later-enqueued task outcomes (seq >= 5) cannot preempt them.
        for event_type, intent in burst:
            await asyncio.wait_for(
                coordinator.mailbox.put(self.user_event(event_type, intent)), TIMEOUT
            )
        consumed = [await self.consume(coordinator) for _ in range(len(burst))]

        self.assertEqual(
            [e.event_type for e in consumed],
            [EventType.USER_INPUT, EventType.USER_INTERRUPT,
             EventType.USER_INTERRUPT, EventType.USER_INTERRUPT],
        )
        self.assertEqual(
            self.transitions(coordinator), [(0, 1), (1, 2), (2, 3), (3, 4)]
        )
        self.assertEqual(coordinator.current_version, 4)
        self.assertEqual(coordinator.state.intent, INTENT_D)
        records = coordinator.registry
        for index, call_id in enumerate(
            ("call_001", "call_002", "call_003", "call_004"), start=1
        ):
            self.assertEqual(records.get(call_id).spawn_version, index)
            if index < 4:
                self.assertTrue(records.get(call_id).cancel_requested)

        # obsolete outcomes arrive afterwards, all rejected as stale
        consumed_outcomes = [
            await self.settle(coordinator, call_id)
            for call_id in ("call_001", "call_002", "call_003")
        ]
        self.assertTrue(
            all(
                e.event_type in (EventType.TASK_CANCELLED, EventType.TASK_COMPLETED)
                for e in consumed_outcomes
            )
        )
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(
            {e.payload["call_id"] for e in rejections},
            {"call_001", "call_002", "call_003"},
        )
        self.assertTrue(all(e.payload["decision"] == "stale" for e in rejections))

        tool.release("call_004")
        await self.settle(coordinator, "call_004")
        self.assertEqual(set(coordinator.accepted_results), {"call_004"})
        self.assertEqual(coordinator.accepted_results["call_004"], f"result:{INTENT_D}")
        self.assertEqual(coordinator.current_version, 4)
        self.assertEqual(coordinator.active_call_ids, ())


class MonotonicityTests(IntegrationTestCase):
    async def test_session_version_monotonicity(self):
        """(15) Mixed inputs, an interruption and outcomes: versions only +1."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")

        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")

        tool.release("call_001")
        await self.settle(coordinator, "call_001")  # stale outcome between bumps

        await self.say(coordinator, EventType.USER_INPUT, INTENT_C)
        await self.wait_started(tool, "call_003")
        await self.settle(coordinator, "call_002")  # cancelled by the bump

        tool.release("call_003")
        await self.settle(coordinator, "call_003")

        observed = [0] + [
            e.payload["to"] for e in self.outbox(coordinator, EventType.STATE_VERSION_CHANGED)
        ]
        for previous, current in zip(observed, observed[1:]):
            self.assertEqual(current, previous + 1)  # strictly +1, never a jump
        self.assertEqual(observed, [0, 1, 2, 3])
        self.assertEqual(coordinator.current_version, observed[-1])
        # each transition's "from" equals the previous "to": no lost update
        froms = [
            e.payload["from"]
            for e in self.outbox(coordinator, EventType.STATE_VERSION_CHANGED)
        ]
        self.assertEqual(froms, [0, 1, 2])
        # acceptance only ever happens against the *current* version
        self.assertIs(
            coordinator.gate.evaluate("call_003", coordinator.current_version).decision,
            Decision.ACCEPTED,
        )
        self.assertEqual(set(coordinator.accepted_results), {"call_003"})
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)


class StaleResultSafetyTests(IntegrationTestCase):
    async def prepare_interrupt_with_late_return(self):
        """Drive to: call_001 late-returned, its outcome not yet consumed."""
        coordinator, tool = self.make()
        tool.program("call_001", "ignore")
        await self.say(coordinator, EventType.USER_INPUT, INTENT_A)
        await self.wait_started(tool, "call_001")
        await self.say(coordinator, EventType.USER_INTERRUPT, INTENT_B)
        await self.wait_started(tool, "call_002")
        tool.release("call_001")
        await asyncio.wait_for(coordinator.supervisor.task_for("call_001"), TIMEOUT)
        return coordinator, tool

    async def test_old_result_is_still_rejected(self):
        """(7) Version mismatch beats everything: STALE, never ACCEPTED."""
        coordinator, tool = await self.prepare_interrupt_with_late_return()

        # the late result exists and the record says SUCCEEDED ...
        self.assertEqual(
            coordinator.registry.get("call_001").state, TaskState.SUCCEEDED
        )
        self.assertIn("Delhi", coordinator.supervisor.result("call_001"))
        # ... and a cancel issued now is too late to change anything
        self.assertFalse(coordinator.supervisor.cancel("call_001"))

        consumed = await self.consume(coordinator)
        self.assertEqual(consumed.event_type, EventType.TASK_COMPLETED)

        decision = coordinator.gate.evaluate("call_001", coordinator.current_version)
        self.assertIs(decision.decision, Decision.STALE)  # not ACCEPTED, not CANCELLED
        self.assertEqual(decision.spawn_version, 1)
        self.assertEqual(decision.current_version, 2)
        rejections = self.outbox(coordinator, EventType.TASK_RESULT_REJECTED)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["call_id"], "call_001")
        self.assertEqual(rejections[0].payload["decision"], "stale")
        self.assertEqual(coordinator.accepted_results, {})

        tool.release("call_002")
        await self.settle(coordinator, "call_002")

    async def test_stale_result_never_mutates_current_state(self):
        """(16) Consuming the stale outcome changes nothing in session state."""
        coordinator, tool = await self.prepare_interrupt_with_late_return()

        snapshot = coordinator.state  # frozen v2 snapshot
        accepted_before = dict(coordinator.accepted_results)
        self.assertEqual(snapshot.version, 2)
        self.assertEqual(snapshot.intent, INTENT_B)

        await self.consume(coordinator)  # the stale outcome is processed

        # identity, version, intent and accepted results all unchanged
        self.assertIs(coordinator.state, snapshot)  # never even replaced
        self.assertEqual(coordinator.state.version, 2)
        self.assertEqual(coordinator.state.intent, INTENT_B)
        self.assertEqual(coordinator.accepted_results, accepted_before)
        self.assertEqual(coordinator.current_version, 2)

        tool.release("call_002")
        await self.settle(coordinator, "call_002")
        self.assertEqual(set(coordinator.accepted_results), {"call_002"})
        self.assertIs(coordinator.state, snapshot)
        self.assertEqual(coordinator.mailbox.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()
