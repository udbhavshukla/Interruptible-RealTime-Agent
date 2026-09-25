"""Tests for the Step 7 versioned session state (aura.runtime.state)."""

import asyncio
import dataclasses
import unittest

from aura.runtime.state import INITIAL_VERSION, SessionState

SESSION = "sess-1"


class InitialStateTests(unittest.TestCase):
    def test_initial_state(self):
        state = SessionState.initial(SESSION)
        self.assertEqual(state.session_id, SESSION)
        self.assertEqual(state.version, INITIAL_VERSION)
        self.assertEqual(state.version, 1)  # deterministic first version
        self.assertIsNone(state.intent)
        self.assertIsNone(state.plan_id)
        self.assertEqual(dict(state.metadata), {})

    def test_initial_state_is_deterministic(self):
        first = SessionState.initial(SESSION, intent="find a flight")
        second = SessionState.initial(SESSION, intent="find a flight")
        self.assertEqual(first.version, second.version)
        self.assertEqual(first, second)

    def test_initial_state_accepts_intent_plan_and_metadata(self):
        state = SessionState.initial(
            SESSION,
            intent="Bangalore to Delhi",
            plan_id="plan_1",
            metadata={"source": "voice"},
        )
        self.assertEqual(state.version, 1)
        self.assertEqual(state.intent, "Bangalore to Delhi")
        self.assertEqual(state.plan_id, "plan_1")
        self.assertEqual(state.metadata["source"], "voice")

    def test_invalid_states_are_rejected(self):
        with self.assertRaises(ValueError):
            SessionState.initial("")
        with self.assertRaises(ValueError):
            SessionState(session_id=SESSION, version=0)
        with self.assertRaises(ValueError):
            SessionState(session_id=SESSION, version=-1)
        with self.assertRaises(ValueError):
            SessionState(session_id=SESSION, version=1, plan_id="")
        with self.assertRaises(TypeError):
            SessionState(session_id=SESSION, version=1, metadata=[1, 2])

    def test_snapshot_is_immutable(self):
        state = SessionState.initial(SESSION)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.version = 2
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.intent = "something else"

    def test_metadata_is_read_only_and_defensive_copied(self):
        source = {"k": "v"}
        state = SessionState.initial(SESSION, metadata=source)
        with self.assertRaises(TypeError):
            state.metadata["k"] = "changed"
        source["k"] = "changed"  # caller mutation must not leak into the snapshot
        self.assertEqual(state.metadata["k"], "v")


class DerivationTests(unittest.TestCase):
    def test_version_increments(self):
        v1 = SessionState.initial(SESSION, intent="Delhi")
        v2 = v1.derive(intent="Mumbai")
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.version, v1.version + 1)

    def test_new_intent_creates_new_version(self):
        v1 = SessionState.initial(SESSION, intent="Bangalore to Delhi")
        v2 = v1.derive(intent="Bangalore to Mumbai")

        self.assertEqual(v2.version, v1.version + 1)
        self.assertEqual(v2.intent, "Bangalore to Mumbai")
        self.assertEqual(v1.intent, "Bangalore to Delhi")  # untouched

    def test_previous_snapshot_remains_unchanged(self):
        v1 = SessionState.initial(
            SESSION, intent="Delhi", plan_id="plan_1", metadata={"k": 1}
        )

        def plain(state: SessionState):
            # dataclasses.asdict cannot deepcopy mappingproxy, so compare
            # the plain field values directly.
            return (
                state.session_id,
                state.version,
                state.intent,
                state.plan_id,
                dict(state.metadata),
            )

        before = plain(v1)

        v2 = v1.derive(intent="Mumbai", plan_id="plan_2", metadata={"k": 2})
        v3 = v2.derive(intent=None, plan_id=None, metadata=None)

        self.assertEqual(plain(v1), before)  # v1 byte-identical
        self.assertEqual(v1.version, 1)
        self.assertEqual(v1.intent, "Delhi")
        self.assertEqual(v1.plan_id, "plan_1")
        self.assertEqual(v1.metadata["k"], 1)
        self.assertIsNot(v2, v1)  # new object every time
        self.assertIsNot(v3, v2)
        # v2 untouched by v3 too
        self.assertEqual(v2.intent, "Mumbai")
        self.assertEqual(v2.plan_id, "plan_2")
        self.assertEqual(v2.metadata["k"], 2)

    def test_session_id_preserved_across_versions(self):
        v1 = SessionState.initial(SESSION, intent="Delhi")
        v2 = v1.derive(intent="Mumbai")
        v3 = v2.derive()
        self.assertEqual(v2.session_id, SESSION)
        self.assertEqual(v3.session_id, SESSION)

    def test_plan_metadata_carries_over_and_clears(self):
        v1 = SessionState.initial(SESSION, plan_id="plan_1", metadata={"a": 1})
        v2 = v1.derive(intent="Mumbai")  # plan/metadata not mentioned

        self.assertEqual(v2.plan_id, "plan_1")  # carried over
        self.assertEqual(v2.metadata["a"], 1)

        v3 = v2.derive(plan_id="plan_2", metadata={"b": 2})  # replaced
        self.assertEqual(v3.plan_id, "plan_2")
        self.assertNotIn("a", v3.metadata)
        self.assertEqual(v3.metadata["b"], 2)
        self.assertEqual(v2.metadata["a"], 1)  # v2 unaffected by replace

        v4 = v3.derive(plan_id=None, metadata=None)  # explicit clear
        self.assertIsNone(v4.plan_id)
        self.assertEqual(dict(v4.metadata), {})

    def test_pure_version_bump_keeps_content(self):
        v1 = SessionState.initial(
            SESSION, intent="Delhi", plan_id="plan_1", metadata={"k": "v"}
        )
        v2 = v1.derive()
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.intent, v1.intent)
        self.assertEqual(v2.plan_id, v1.plan_id)
        self.assertEqual(dict(v2.metadata), dict(v1.metadata))

    def test_version_is_minted_not_chosen(self):
        v1 = SessionState.initial(SESSION)
        with self.assertRaises(TypeError):  # no version parameter exists
            v1.derive(version=99)

    def test_versions_are_monotonically_increasing(self):
        states = [SessionState.initial(SESSION, intent="intent-0")]
        for i in range(1, 10):
            states.append(states[-1].derive(intent=f"intent-{i}"))

        versions = [s.version for s in states]
        self.assertEqual(versions, list(range(1, 11)))  # 1..10, unique, gap-free
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(set(versions)), len(versions))


class SnapshotRetentionTests(unittest.IsolatedAsyncioTestCase):
    """Requirement: async tasks retain the version they were spawned from."""

    async def test_async_task_retains_spawn_version(self):
        v1 = SessionState.initial(SESSION, intent="Bangalore to Delhi")
        captured: list[SessionState] = []

        async def tool_task(spawn_snapshot: SessionState) -> None:
            # Runs later; reads only the snapshot it was handed.
            await asyncio.sleep(0)  # deterministic single yield, not a delay
            captured.append(spawn_snapshot)

        task = asyncio.create_task(tool_task(v1))

        # Meanwhile the session moves on (user correction).
        v2 = v1.derive(intent="Bangalore to Mumbai")
        self.assertEqual(v2.version, 2)

        await task
        self.assertIs(captured[0], v1)  # same object, same version
        self.assertEqual(captured[0].version, 1)
        self.assertEqual(captured[0].intent, "Bangalore to Delhi")

    async def test_spawn_version_drives_acceptance(self):
        # Step 6 + Step 7 integration: a task spawned under v1 must be STALE
        # once the session snapshot has advanced to v2.
        from aura.runtime.acceptance import AcceptanceGate, Decision
        from aura.runtime.registry import TaskRegistry

        v1 = SessionState.initial(SESSION, intent="Delhi")

        registry = TaskRegistry()
        registry.register(
            call_id="call_001",
            tool_name="flight_search",
            spawn_version=v1.version,  # retains the spawn version
        )
        registry.mark_running("call_001")
        registry.mark_succeeded("call_001")
        gate = AcceptanceGate(registry)

        # Session advances: user correction -> v2.
        v2 = v1.derive(intent="Mumbai")
        self.assertEqual(v2.version, 2)

        # Late v1 result at current version 2 -> STALE, never ACCEPTED.
        self.assertIs(
            gate.evaluate("call_001", current_version=v2.version).decision,
            Decision.STALE,
        )
        # The same record would have been accepted at its own spawn version.
        self.assertIs(
            gate.evaluate("call_001", current_version=v1.version).decision,
            Decision.ACCEPTED,
        )


if __name__ == "__main__":
    unittest.main()
