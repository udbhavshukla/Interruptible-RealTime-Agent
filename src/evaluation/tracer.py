from __future__ import annotations

import json
from pathlib import Path


class Tracer:
    """Record structured trace events for evaluation purposes.

    The tracer does not implement any agent logic; it simply records
    events that other components emit so the evaluation harness can
    observe them.  Events are plain dicts with at minimum ``type`` and
    ``timestamp``; ``payload`` is optional user data.

    The class supports JSONL output for deterministic, reversible
    persistence.
    """

    def __init__(self) -> None:
        self._events: list[dict] = []

    def record(self, event: dict) -> None:
        """Append a single event dict to the trace.

        The event dict must contain at minimum ``type`` and ``timestamp``.
        A ``payload`` key is optional and can hold any serialisable data.

        Raises
        ------
        ValueError
            If ``event`` is missing ``type`` or ``timestamp``.
        """
        if "type" not in event:
            raise ValueError("event dict must contain a ``type`` key")
        if "timestamp" not in event:
            raise ValueError("event dict must contain a ``timestamp`` key")
        self._events.append(event)

    def get_events(self) -> list[dict]:
        """Return a copy of the recorded events in order."""
        return list(self._events)

    def clear(self) -> None:
        """Reset the trace, ready for a new run."""
        self._events.clear()

    def write_jsonl(self, path: str | Path) -> None:
        """Write all recorded events as JSONL (one JSON object per line).

        The output is deterministic: keys are serialised in the order
        they appear in the dict, but ``json.dumps`` uses ``sort_keys=False``
        so the original ordering is preserved.

        Parameters
        ----------
        path : str | Path
            File path where the JSONL will be written.  The file is
            overwritten if it already exists.
        """
        path = Path(path)
        with path.open("w", encoding="utf-8") as fh:
            for ev in self._events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")