from src.evaluation.tracer import Tracer


def test_record_and_get_events():
    tracer = Tracer()
    event1 = {"type": "user_say", "timestamp": 1.0, "payload": {"text": "hello"}}
    event2 = {"type": "tool_result", "timestamp": 2.0, "payload": {"result": 42}}

    tracer.record(event1)
    tracer.record(event2)

    events = tracer.get_events()
    assert len(events) == 2
    assert events[0] is event1
    assert events[1] is event2


def test_clear():
    tracer = Tracer()
    tracer.record({"type": "a", "timestamp": 0.0})
    assert len(tracer.get_events()) == 1
    tracer.clear()
    assert len(tracer.get_events()) == 0


def test_record_missing_type():
    """Record should raise ValueError when event dict lacks ``type``."""
    tracer = Tracer()
    try:
        tracer.record({"timestamp": 1.0})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_record_missing_timestamp():
    """Record should raise ValueError when event dict lacks ``timestamp``."""
    tracer = Tracer()
    try:
        tracer.record({"type": "foo"})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_jsonl_roundtrip():
    """JSONL output should be round‑trip consistent with recorded events."""
    import json
    import os
    import tempfile

    tracer = Tracer()
    events = [
        {"type": "user_say", "timestamp": 1.0, "payload": {"text": "hello"}},
        {"type": "tool_result", "timestamp": 2.0, "payload": {"result": 42}},
    ]
    for ev in events:
        tracer.record(ev)
    # Write JSONL to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
    tracer.write_jsonl(path)
    # Read back and verify each line
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == len(events), "JSONL line count mismatch"
    for line, expected in zip(lines, events):
        parsed = json.loads(line.strip())
        assert parsed == expected, f"Round‑trip mismatch: {parsed} != {expected}"
    # Cleanup
    os.remove(path)


def test_record_missing_type_raises_value_error():
    """Recording an event without ``type`` should raise ValueError."""
    tracer = Tracer()
    try:
        tracer.record({"timestamp": 1.0})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_record_missing_timestamp_raises_value_error():
    """Recording an event without ``timestamp`` should raise ValueError."""
    tracer = Tracer()
    try:
        tracer.record({"type": "foo"})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass