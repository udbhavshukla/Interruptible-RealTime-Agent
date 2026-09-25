"""Tests for the Step 2 event model (aura.runtime.events)."""

import dataclasses
import time
import unittest

from aura.runtime.events import Event, EventType, new_event_id


class EventTypeTests(unittest.TestCase):
    REQUIRED = {
        "USER_INPUT",
        "USER_INTERRUPT",
        "TASK_STARTED",
        "TASK_COMPLETED",
        "TASK_FAILED",
        "TASK_CANCEL_REQUESTED",
        "TASK_CANCELLED",
        "TASK_RESULT_REJECTED",
        "STATE_VERSION_CHANGED",
        "PLAN_READY",
        "PLAN_FAILED",
        "RUNTIME_ERROR",
    }

    def test_required_event_types_exist(self):
        self.assertTrue(self.REQUIRED.issubset(set(EventType.__members__)))

    def test_event_type_is_string_enum(self):
        self.assertIsInstance(EventType.USER_INPUT, str)
        self.assertEqual(EventType.USER_INPUT.value, "user.input")
        self.assertEqual(EventType(EventType.TASK_COMPLETED), EventType.TASK_COMPLETED)

    def test_event_type_string_roundtrip(self):
        for member in EventType:
            self.assertEqual(EventType(member.value), member)

    def test_event_type_in_event_is_coerced_from_string(self):
        event = Event(session_id="s1", event_type="task.completed")
        self.assertIs(event.event_type, EventType.TASK_COMPLETED)


class EventConstructionTests(unittest.TestCase):
    def test_event_can_be_constructed(self):
        event = Event(session_id="s1", event_type=EventType.USER_INPUT)
        self.assertIsInstance(event, Event)

    def test_event_contains_all_required_fields(self):
        now = time.time()
        event = Event(
            event_id="evt-1",
            session_id="sess-1",
            seq=7,
            event_type=EventType.TASK_COMPLETED,
            timestamp=now,
            actor="task:call_001",
            payload={"result": "DEL"},
            correlation_id="corr-1",
            causation_id="cause-1",
            version=2,
        )
        self.assertEqual(event.event_id, "evt-1")
        self.assertEqual(event.session_id, "sess-1")
        self.assertEqual(event.seq, 7)
        self.assertIs(event.event_type, EventType.TASK_COMPLETED)
        self.assertEqual(event.timestamp, now)
        self.assertEqual(event.actor, "task:call_001")
        self.assertEqual(event.payload["result"], "DEL")
        self.assertEqual(event.correlation_id, "corr-1")
        self.assertEqual(event.causation_id, "cause-1")
        self.assertEqual(event.version, 2)

    def test_event_has_defaults(self):
        event = Event(session_id="s1", event_type=EventType.USER_INPUT)
        self.assertGreater(len(event.event_id), 0)
        self.assertEqual(event.seq, 0)
        self.assertEqual(event.actor, "system")
        self.assertEqual(dict(event.payload), {})
        self.assertIsNone(event.correlation_id)
        self.assertIsNone(event.causation_id)
        self.assertEqual(event.version, 1)
        self.assertGreater(event.timestamp, 0)

    def test_event_is_immutable(self):
        event = Event(session_id="s1", event_type=EventType.USER_INPUT)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.seq = 99
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.payload = {}

    def test_payload_mapping_is_read_only(self):
        source = {"city": "Delhi"}
        event = Event(session_id="s1", event_type=EventType.USER_INPUT, payload=source)
        with self.assertRaises(TypeError):
            event.payload["city"] = "Mumbai"
        source["city"] = "Mumbai"  # caller mutation must not leak into the event
        self.assertEqual(event.payload["city"], "Delhi")

    def test_payload_can_contain_simple_data(self):
        event = Event(
            session_id="s1",
            event_type=EventType.USER_INPUT,
            payload={"text": "hello", "n": 3, "tags": ["a", "b"], "ok": True},
        )
        self.assertEqual(event.payload["text"], "hello")
        self.assertEqual(event.payload["n"], 3)
        self.assertEqual(event.payload["tags"], ["a", "b"])
        self.assertIs(event.payload["ok"], True)

    def test_two_events_have_different_ids(self):
        a = Event(session_id="s1", event_type=EventType.USER_INPUT)
        b = Event(session_id="s1", event_type=EventType.USER_INPUT)
        self.assertNotEqual(a.event_id, b.event_id)
        self.assertNotEqual(new_event_id(), new_event_id())

    def test_invalid_events_are_rejected(self):
        with self.assertRaises(ValueError):
            Event(session_id="", event_type=EventType.USER_INPUT)
        with self.assertRaises(ValueError):
            Event(session_id="s1", event_type=EventType.USER_INPUT, event_id="")
        with self.assertRaises(ValueError):
            Event(session_id="s1", event_type=EventType.USER_INPUT, seq=-1)
        with self.assertRaises(ValueError):
            Event(session_id="s1", event_type=EventType.USER_INPUT, version=-1)
        with self.assertRaises(TypeError):
            Event(session_id="s1", event_type=EventType.USER_INPUT, payload=[1, 2])
        with self.assertRaises(ValueError):
            Event(session_id="s1", event_type="not.a.real.type")


if __name__ == "__main__":
    unittest.main()
