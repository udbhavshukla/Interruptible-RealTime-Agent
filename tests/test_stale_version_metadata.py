"""Regression tests: stale-result events carry both version numbers.

The execution panel renders a rejection as
``v{resultVersion} vs current v{currentVersion}`` (LiveAgent.tsx:205).
``RealAgentClient`` sources those two values from the serialized frame's
``payload.spawn_version`` (the superseded call's version) and its
top-level ``state_version`` (the current version at emission) — the
shape ``MockAgentClient.ts:150`` emits for drop-in parity.

These tests pin the backend contract both layers depend on: if either
value ever disappears from the emitted event or the wire frame, the
panel regresses to "undefined vs current undefined". No hardcoded
versions beyond the scripted demo's own scenario (v1 superseded by v2),
no sleeps — ``run_demo`` is gate-driven.

See also: frontend mapping in RealAgentClient.ts (no JS test runner is
configured — ``package.json`` has only dev/build/preview scripts — so
the frontend side is verified by ``tsc --noEmit`` during the build).
"""

from __future__ import annotations

import types
import unittest

from aura.runtime.demo import run_demo
from aura.runtime.events import EventType

try:
    from aura.runtime import websocket_server as ws
except Exception as exc:  # pragma: no cover - optional server dependencies
    ws = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class StaleRejectionEmissionTests(unittest.IsolatedAsyncioTestCase):
    """The emitted TASK_RESULT_REJECTED event carries both versions."""

    async def test_rejection_event_carries_original_and_current_version(
        self,
    ) -> None:
        report = await run_demo()
        rejections = [
            e
            for e in report.coordinator.emitted
            if e.event_type is EventType.TASK_RESULT_REJECTED
        ]
        self.assertEqual(len(rejections), 1)
        event = rejections[0]

        # The original operation's version (spawn tagging, coordinator.py).
        self.assertEqual(event.payload["call_id"], "call_001")
        self.assertEqual(event.payload["decision"], "stale")
        self.assertEqual(event.payload["spawn_version"], 1)

        # The current session version at emission (stamped by _emit).
        self.assertEqual(event.version, report.final_version)
        self.assertEqual(event.version, 2)


@unittest.skipIf(ws is None, f"websocket_server unavailable: {_IMPORT_ERROR}")
class StaleFrameSerializationTests(unittest.IsolatedAsyncioTestCase):
    """The wire frame keeps both versions where the frontend reads them."""

    async def test_frame_exposes_spawn_version_and_state_version(self) -> None:
        report = await run_demo()
        rejection = next(
            e
            for e in report.coordinator.emitted
            if e.event_type is EventType.TASK_RESULT_REJECTED
        )
        session = types.SimpleNamespace(
            session_id="sess_stale_contract", tool_name="flight_search"
        )

        frame = ws._serialize_event(rejection, session)

        self.assertEqual(frame["type"], "STALE_RESULT_REJECTED")
        self.assertEqual(frame["call_id"], "call_001")
        # Sources RealAgentClient.ts maps into detail.resultVersion /
        # detail.currentVersion for the "v1 vs current v2" row:
        self.assertEqual(frame["payload"]["spawn_version"], 1)
        self.assertEqual(frame["state_version"], 2)


if __name__ == "__main__":
    unittest.main()
