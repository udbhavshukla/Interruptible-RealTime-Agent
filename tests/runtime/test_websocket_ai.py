"""End-to-end AI workflow tests through the WebSocket server seams.

Drives ``_open_session`` / ``_submit`` / ``_forward`` with a fake socket, so
the whole served path runs in-process: user fact -> Coordinator -> tool ->
acceptance gate -> responder (model provider) -> FINAL_RESPONSE frame.

A mock provider stands in for the model, so the model *path* is verified
without a network or API key. The live-provider conversation itself remains
unverified here (no credentials are available in this environment).
"""

from __future__ import annotations

import asyncio
import json
import unittest

from aura.assistant.responder import AssistantResponder
from aura.llm.mock_llm import MockLLMProvider
from aura.runtime import websocket_server as ws
from aura.runtime.events import EventType
from aura.runtime.mailbox import Priority


class _FakeSocket:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed: bool = False

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


def _model_responder() -> AssistantResponder:
    return AssistantResponder(
        MockLLMProvider(reply=lambda _messages: "MODEL ANSWER"),
        fallback=ws._answer_text,
    )


class AIWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._orig_delay = ws.FLIGHT_SEARCH_DELAY_S
        self._orig_responder = ws._RESPONDER
        ws.FLIGHT_SEARCH_DELAY_S = 0.05
        ws._RESPONDER = _model_responder()
        self._sessions: list = []

    async def asyncTearDown(self) -> None:
        for session in self._sessions:
            await ws._close_session(session)
        ws.FLIGHT_SEARCH_DELAY_S = self._orig_delay
        ws._RESPONDER = self._orig_responder

    async def _open(self, session_id: str):
        fake = _FakeSocket()
        session = ws._open_session(session_id, fake)  # type: ignore[arg-type]
        self._sessions.append(session)
        return session, fake

    async def _await_frame(self, fake: _FakeSocket, frame_type: str, timeout: float = 4.0) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for frame in fake.frames:
                if frame.get("type") == frame_type:
                    return frame
            await asyncio.sleep(0.02)
        seen = [f.get("type") for f in fake.frames]
        raise AssertionError(f"{frame_type} never arrived; saw {seen}")

    async def test_model_answer_reaches_final_response(self) -> None:
        session, fake = await self._open("e2e-model")
        await ws._submit(session, EventType.USER_INPUT, "Find flight Bangalore to Delhi", Priority.DATA)
        final = await self._await_frame(fake, "FINAL_RESPONSE")
        self.assertEqual(final["payload"]["auraText"], "MODEL ANSWER")
        self.assertEqual(final["payload"]["snapshot"]["status"], "READY")
        self.assertEqual(final["payload"]["provider"], "mock")
        self.assertNotIn("simulated", final["payload"]["auraText"].lower())

    async def test_offline_responder_uses_deterministic_answer(self) -> None:
        ws._RESPONDER = AssistantResponder(fallback=ws._answer_text)
        session, fake = await self._open("e2e-offline")
        await ws._submit(session, EventType.USER_INPUT, "Find flight Bangalore to Delhi", Priority.DATA)
        final = await self._await_frame(fake, "FINAL_RESPONSE")
        self.assertIn("simulated", final["payload"]["auraText"].lower())
        self.assertEqual(final["payload"]["provider"], "none")

    async def test_interrupt_supersedes_and_only_latest_answer_is_sent(self) -> None:
        session, fake = await self._open("e2e-interrupt")
        await ws._submit(
            session, EventType.USER_INPUT, "Find flight Bangalore to Delhi", Priority.DATA
        )
        await asyncio.sleep(0.01)  # let the first search start
        await ws._submit(
            session,
            EventType.USER_INTERRUPT,
            "Actually find flight Bangalore to Mumbai",
            Priority.CONTROL,
        )
        final = await self._await_frame(fake, "FINAL_RESPONSE")

        # Let any late/stale emission surface before asserting there is none.
        await asyncio.sleep(0.3)
        finals = [f for f in fake.frames if f.get("type") == "FINAL_RESPONSE"]
        self.assertEqual(len(finals), 1, "a superseded call must not also answer")
        self.assertEqual(final["payload"]["snapshot"]["destination"].lower(), "mumbai")
        self.assertTrue(
            any(f.get("type") == "STALE_RESULT_REJECTED" for f in fake.frames)
            or any(f.get("type") == "TOOL_CANCELLED" for f in fake.frames),
            "the superseded search should be cancelled or rejected, not answered",
        )

    async def test_rapid_successive_messages_answer_only_the_latest(self) -> None:
        session, fake = await self._open("e2e-rapid")
        await ws._submit(
            session, EventType.USER_INPUT, "Find flight Bangalore to Delhi", Priority.DATA
        )
        await asyncio.sleep(0.005)
        await ws._submit(
            session, EventType.USER_INTERRUPT, "Actually Chennai", Priority.CONTROL
        )
        await asyncio.sleep(0.005)
        await ws._submit(
            session, EventType.USER_INTERRUPT, "Actually Kochi", Priority.CONTROL
        )

        final = await self._await_frame(fake, "FINAL_RESPONSE")
        await asyncio.sleep(0.3)  # let every late outcome settle
        finals = [f for f in fake.frames if f.get("type") == "FINAL_RESPONSE"]
        self.assertEqual(len(finals), 1, "only the newest intent may answer")
        self.assertEqual(final["payload"]["snapshot"]["destination"].lower(), "kochi")
        self.assertEqual(final["payload"]["provider"], "mock")

        # Cancellation stays truthful: no cancellation/rejection may name the
        # winning call, and every superseded call is reported, not answered.
        winning = final.get("call_id")
        for frame in fake.frames:
            if frame.get("type") in {"TOOL_CANCELLED", "STALE_RESULT_REJECTED"}:
                self.assertNotEqual(frame.get("call_id"), winning)
        superseded = {
            f.get("call_id")
            for f in fake.frames
            if f.get("type") in {"TOOL_CANCELLED", "STALE_RESULT_REJECTED"}
        }
        self.assertTrue(superseded, "superseded calls must be reported")
        self.assertNotIn(winning, superseded)
