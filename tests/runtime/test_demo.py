"""Step 9 tests: end-to-end interruptible runtime demo.

Verifies the required scenario guarantees:

- the old task cannot overwrite the new intent,
- the new task is accepted,
- the version transition occurs (v1 -> v2),
- cancellation is attempted (but stays advisory),
- the late result is rejected (STALE),
- the final state is Mumbai,

plus the presentation artifact (exact trace) and determinism (two runs,
byte-identical output). No sleeps anywhere: the demo is gate-driven and
every wait is a ``wait_for`` timeout guard.
"""

import asyncio
import contextlib
import io
import unittest

from aura.runtime import demo
from aura.runtime.acceptance import Decision
from aura.runtime.demo import (
    DEFAULT_TIMEOUT,
    FLIGHT_TO_DELHI,
    FLIGHT_TO_MUMBAI,
    INTENT_DELHI,
    INTENT_MUMBAI,
    DemoFlightTool,
    FlightQuote,
    run_demo,
)
from aura.runtime.events import EventType
from aura.runtime.registry import TaskState
from aura.runtime.state import SessionState

#: The exact presentation output the hackathon demo must print.
EXPECTED_TRACE = (
    "[USER] Find flight Bangalore to Delhi",
    "[STATE] version=1",
    "[TOOL] call_001 started",
    "",
    "[USER] Actually Mumbai",
    "[COORDINATOR] Interrupt detected",
    "[STATE] v1 → v2",
    "[CANCEL] call_001 cancellation requested",
    "[TOOL] call_002 started",
    "",
    "[TOOL] call_001 returned late",
    "[ACCEPTANCE] call_001 → STALE_REJECTED",
    "",
    "[TOOL] call_002 completed",
    "[ACCEPTANCE] call_002 → ACCEPTED",
    "",
    "[FINAL] Bangalore → Mumbai",
)


class DemoScenarioTests(unittest.IsolatedAsyncioTestCase):
    """Run the complete demo once per test and assert each invariant."""

    async def asyncSetUp(self) -> None:
        self.report = await run_demo()
        self.coordinator = self.report.coordinator

    # -------------------------------------------------------- presentation
    async def test_trace_matches_presentation_exactly(self):
        self.assertEqual(self.report.trace, EXPECTED_TRACE)
        self.assertEqual(self.report.text.splitlines(), list(EXPECTED_TRACE))

    # ------------------------------------------------------- version (T1/T6)
    async def test_version_transition_occurs(self):
        transitions = [
            (event.payload["from"], event.payload["to"])
            for event in self.coordinator.emitted
            if event.event_type is EventType.STATE_VERSION_CHANGED
        ]
        self.assertEqual(transitions, [(0, 1), (1, 2)])
        self.assertEqual(self.report.final_version, 2)
        self.assertEqual(self.coordinator.state.version, 2)

    async def test_work_is_tagged_with_its_version(self):
        # I3: call_001 belongs to v1, call_002 to the corrected v2.
        self.assertEqual(self.coordinator.registry.get("call_001").spawn_version, 1)
        self.assertEqual(self.coordinator.registry.get("call_002").spawn_version, 2)

    # ---------------------------------------------------- cancellation (T5)
    async def test_cancellation_is_attempted(self):
        record = self.coordinator.registry.get("call_001")
        self.assertTrue(record.cancel_requested)  # attempted: tombstone first
        cancels = [
            event
            for event in self.coordinator.emitted
            if event.event_type is EventType.TASK_CANCEL_REQUESTED
        ]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].payload["call_id"], "call_001")
        self.assertEqual(cancels[0].payload["outcome"], "requested")
        # Advisory: the tool ignored it and still returned a result (T8).
        self.assertEqual(record.state, TaskState.SUCCEEDED)

    # ------------------------------------------------- late result (T8/T9)
    async def test_late_result_is_rejected_as_stale(self):
        decision = self.coordinator.gate.evaluate(
            "call_001", self.report.final_version
        )
        self.assertIs(decision.decision, Decision.STALE)
        rejections = [
            event
            for event in self.coordinator.emitted
            if event.event_type is EventType.TASK_RESULT_REJECTED
        ]
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].payload["call_id"], "call_001")
        self.assertEqual(rejections[0].payload["decision"], "stale")

    async def test_old_task_cannot_overwrite_new_intent(self):
        # The stale quote really exists...
        stale_quote = self.coordinator.supervisor.result("call_001")
        self.assertEqual(stale_quote.destination, "Delhi")
        # ...but it never reaches the session's accepted results or state.
        self.assertNotIn("call_001", self.coordinator.accepted_results)
        self.assertEqual(self.report.final_intent, INTENT_MUMBAI)
        self.assertEqual(self.report.final_version, 2)

    # ------------------------------------------------- new result (T10/T11)
    async def test_new_task_is_accepted(self):
        self.assertIn("call_002", self.coordinator.accepted_results)
        quote = self.coordinator.accepted_results["call_002"]
        self.assertIsInstance(quote, FlightQuote)
        self.assertEqual(quote, FLIGHT_TO_MUMBAI)
        decision = self.coordinator.gate.evaluate(
            "call_002", self.report.final_version
        )
        self.assertIs(decision.decision, Decision.ACCEPTED)

    # ----------------------------------------------------------- final (T12)
    async def test_final_state_is_mumbai(self):
        self.assertIn("Mumbai", self.report.final_intent)
        self.assertEqual(self.report.final_route, "Bangalore → Mumbai")
        self.assertEqual(self.report.final_result, FLIGHT_TO_MUMBAI)

    async def test_runtime_is_fully_settled(self):
        self.assertEqual(self.coordinator.active_call_ids, ())
        self.assertEqual(self.coordinator.mailbox.unfinished_tasks, 0)
        self.assertEqual(
            {
                self.coordinator.registry.get("call_001").state,
                self.coordinator.registry.get("call_002").state,
            },
            {TaskState.SUCCEEDED},
        )


class DemoMockToolTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the deterministic stand-in "flight API"."""

    async def test_current_search_waits_for_release(self):
        tool = DemoFlightTool()
        state = SessionState.initial("probe", intent=INTENT_MUMBAI)
        started = tool.started.setdefault("call_probe", asyncio.Event())
        task = asyncio.create_task(
            tool(intent=INTENT_MUMBAI, call_id="call_probe", state=state)
        )
        await asyncio.wait_for(started.wait(), DEFAULT_TIMEOUT)
        self.assertFalse(task.done())  # blocked until released, not timed
        tool.release()
        quote = await asyncio.wait_for(task, DEFAULT_TIMEOUT)
        self.assertEqual(quote, FLIGHT_TO_MUMBAI)

    async def test_obsolete_search_ignores_cancellation_and_returns_late(self):
        tool = DemoFlightTool()
        state = SessionState.initial("probe", intent=INTENT_DELHI)
        started = tool.started.setdefault("call_probe", asyncio.Event())
        task = asyncio.create_task(
            tool(intent=INTENT_DELHI, call_id="call_probe", state=state)
        )
        await asyncio.wait_for(started.wait(), DEFAULT_TIMEOUT)
        task.cancel()  # advisory cancel from the demo timeline (T5)
        quote = await asyncio.wait_for(task, DEFAULT_TIMEOUT)
        self.assertEqual(quote, FLIGHT_TO_DELHI)  # T8: late result materialises

    async def test_quotes_are_pure_data_no_network(self):
        self.assertEqual(FLIGHT_TO_DELHI.route, "Bangalore → Delhi")
        self.assertEqual(FLIGHT_TO_MUMBAI.route, "Bangalore → Mumbai")
        self.assertIsInstance(FLIGHT_TO_DELHI, FlightQuote)


class DemoDeterminismTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_runs_produce_identical_traces(self):
        first = await run_demo()
        second = await run_demo()
        self.assertEqual(first.trace, second.trace)
        self.assertEqual(first.final_version, second.final_version)
        self.assertEqual(first.final_route, second.final_route)

    async def test_demo_uses_only_the_mock_tool(self):
        report = await run_demo()
        record_1 = report.coordinator.registry.get("call_001")
        record_2 = report.coordinator.registry.get("call_002")
        self.assertEqual(record_1.tool_name, "mock_flight_search")
        self.assertEqual(record_2.tool_name, "mock_flight_search")


class DemoEntrypointTests(unittest.TestCase):
    def test_main_prints_the_expected_trace(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            demo.main()
        self.assertEqual(buffer.getvalue().splitlines(), list(EXPECTED_TRACE))


if __name__ == "__main__":
    unittest.main()
