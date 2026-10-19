"""Provider-layer tests.

The OpenAI-compatible provider is exercised against a throwaway local HTTP
server that speaks the ``/chat/completions`` protocol (both JSON and SSE).
That verifies the real HTTP/serialization path without a network or an API
key. A live vendor is intentionally not contacted.

The suite has no async plugin (the repo uses ``unittest.IsolatedAsyncioTestCase``
elsewhere), so these tests drive the coroutines with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aura.llm.base import LLMError, Message, ProviderUnavailable
from aura.llm.config import ProviderConfig
from aura.llm.mock_llm import MockLLMProvider
from aura.llm.openai_compatible import OpenAICompatibleProvider


async def _collect(aiter) -> list:
    return [item async for item in aiter]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        self.server.last_body = json.loads(raw or b"{}")
        self.server.last_auth = self.headers.get("Authorization")
        mode = self.server.mode

        if mode == "ok":
            self._json(200, {"choices": [{"message": {"content": "hello from fake"}}]})
        elif mode == "empty":
            self._json(200, {"choices": [{"message": {"content": "   "}}]})
        elif mode == "malformed":
            self._json(200, {"unexpected": True})
        elif mode == "auth":
            self._json(403, {"error": "bad key"})
        elif mode == "err":
            self._json(500, {"error": "boom"})
        elif mode == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("Hel", "lo", " world"):
                event = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        elif mode == "stream_auth":
            self._json(401, {"error": "bad key"})
        else:  # pragma: no cover - guard against a typo in a test
            self._json(404, {"error": "unknown mode"})

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # silence test output
        pass


@pytest.fixture
def fake_server():
    servers = []

    def _start(mode: str):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.mode = mode  # type: ignore[attr-defined]
        httpd.last_body = None  # type: ignore[attr-defined]
        httpd.last_auth = None  # type: ignore[attr-defined]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append((httpd, thread))
        host, port = httpd.server_address
        return httpd, f"http://{host}:{port}/v1"

    yield _start

    for httpd, thread in servers:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _provider(fake_server, mode: str, *, api_key: str = "sk-test"):
    httpd, base = fake_server(mode)
    provider = OpenAICompatibleProvider(
        base_url=base, api_key=api_key, model="test-model", timeout_s=5.0
    )
    return httpd, provider


MSGS = [Message("system", "be brief"), Message("user", "hi")]


# --------------------------------------------------------------- Message/base
def test_message_rejects_unknown_role():
    with pytest.raises(ValueError):
        Message("tool", "x")


def test_message_rejects_non_string_content():
    with pytest.raises(TypeError):
        Message("user", 5)  # type: ignore[arg-type]


# ------------------------------------------------------------------- config
def test_config_defaults_to_offline():
    config = ProviderConfig.from_env({})
    assert config.provider == "none"
    assert config.enabled is False


def test_config_reads_env_and_redacts_key():
    config = ProviderConfig.from_env(
        {
            "AURA_LLM_PROVIDER": "openai",
            "AURA_LLM_API_KEY": "sk-secret",
            "AURA_LLM_MODEL": "gpt-x",
            "AURA_LLM_TIMEOUT_S": "12",
        }
    )
    assert config.provider == "openai"
    assert config.enabled is True
    assert config.timeout_s == 12
    assert config.redacted()["api_key"] == "***"
    assert "sk-secret" not in json.dumps(config.redacted())


def test_config_tolerates_garbage_and_unknown_provider():
    config = ProviderConfig.from_env(
        {"AURA_LLM_PROVIDER": "wat", "AURA_LLM_TIMEOUT_S": "not-a-number"}
    )
    assert config.provider == "none"
    assert config.timeout_s == 30.0


# --------------------------------------------------------------------- mock
def test_mock_provider_is_deterministic_and_says_so():
    provider = MockLLMProvider()
    out = asyncio.run(provider.complete([Message("user", "hello there")]))
    assert out.startswith("[mock]")
    assert "hello there" in out


def test_mock_provider_streams_the_whole_text():
    provider = MockLLMProvider()
    msgs = [Message("user", "a b c")]
    chunks = asyncio.run(_collect(provider.stream(msgs)))
    assert "".join(chunks) == asyncio.run(provider.complete(msgs))


# --------------------------------------------------------- openai-compatible
def test_complete_returns_content_and_sends_auth_and_payload(fake_server):
    httpd, provider = _provider(fake_server, "ok")
    text = asyncio.run(provider.complete(MSGS))
    assert text == "hello from fake"
    assert httpd.last_auth == "Bearer sk-test"
    assert httpd.last_body["model"] == "test-model"
    assert httpd.last_body["messages"][0] == {"role": "system", "content": "be brief"}
    assert httpd.last_body["stream"] is False


def test_complete_maps_auth_failure_to_provider_unavailable(fake_server):
    _, provider = _provider(fake_server, "auth")
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.complete(MSGS))


def test_complete_maps_server_error_to_llm_error(fake_server):
    _, provider = _provider(fake_server, "err")
    with pytest.raises(LLMError):
        asyncio.run(provider.complete(MSGS))


def test_complete_rejects_malformed_body(fake_server):
    _, provider = _provider(fake_server, "malformed")
    with pytest.raises(LLMError):
        asyncio.run(provider.complete(MSGS))


def test_complete_rejects_empty_answer(fake_server):
    _, provider = _provider(fake_server, "empty")
    with pytest.raises(LLMError):
        asyncio.run(provider.complete(MSGS))


def test_complete_wraps_unreachable_endpoint():
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:1/v1", api_key=None, model="m", timeout_s=1.0
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.complete(MSGS))


def test_stream_yields_incremental_deltas(fake_server):
    _, provider = _provider(fake_server, "stream")
    chunks = asyncio.run(_collect(provider.stream(MSGS)))
    assert chunks == ["Hel", "lo", " world"]
    assert "".join(chunks) == "Hello world"


def test_stream_maps_auth_failure(fake_server):
    _, provider = _provider(fake_server, "stream_auth")
    with pytest.raises(ProviderUnavailable):
        asyncio.run(_collect(provider.stream(MSGS)))


def test_provider_requires_base_url_and_model():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(base_url="", api_key="k", model="m")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(base_url="http://x/v1", api_key="k", model="")
