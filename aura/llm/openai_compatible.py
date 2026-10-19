"""OpenAI-compatible chat provider.

Talks the ``/chat/completions`` protocol used by OpenAI and by many
gateways (vLLM, Ollama, LM Studio, Azure-compatible shims). Uses only the
standard library (``urllib``) in a worker thread, so AURA gains a real model
dependency without adding a runtime package.

Design notes
------------
- ``complete`` posts once and returns the message content.
- ``stream`` reads the SSE ``data:`` lines on a background thread and
  forwards deltas through an :class:`asyncio.Queue`, so the event loop is
  never blocked.
- HTTP 401/403 and connection failures raise :class:`ProviderUnavailable`;
  other non-2xx responses, malformed bodies and timeouts raise
  :class:`LLMError`. :class:`asyncio.CancelledError` is never swallowed.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from typing import AsyncIterator, Dict, Optional, Sequence

from .base import LLMError, Message, ProviderUnavailable

__all__ = ["OpenAICompatibleProvider"]

_DONE = object()


class OpenAICompatibleProvider:
    """Provider for any OpenAI-style ``/chat/completions`` endpoint."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str],
        model: str,
        timeout_s: float = 30.0,
    ) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("base_url must be a non-empty URL")
        if not model:
            raise ValueError("model must be a non-empty identifier")
        self._base = base
        self._model = model
        self._timeout = float(timeout_s)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._headers = headers

    # -- internals ---------------------------------------------------------
    def _payload(self, messages: Sequence[Message], *, stream: bool) -> Dict:
        return {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
        }

    def _post(self, body: Dict, timeout: float) -> str:
        req = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            if exc.code in (401, 403):
                raise ProviderUnavailable(
                    f"provider rejected credentials (HTTP {exc.code})"
                ) from exc
            raise LLMError(f"provider HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"provider unreachable: {exc}") from exc

    @staticmethod
    def _content_from(body: str) -> str:
        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed provider response: {exc}") from exc

    # -- public API --------------------------------------------------------
    async def complete(
        self, messages: Sequence[Message], *, timeout: Optional[float] = None
    ) -> str:
        limit = self._timeout if timeout is None else float(timeout)
        body = self._payload(messages, stream=False)
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._post, body, limit), timeout=limit + 5.0
            )
        except asyncio.TimeoutError as exc:
            raise LLMError("provider timed out") from exc
        content = self._content_from(raw).strip()
        if not content:
            raise LLMError("provider returned an empty answer")
        return content

    async def stream(
        self, messages: Sequence[Message], *, timeout: Optional[float] = None
    ) -> AsyncIterator[str]:
        limit = self._timeout if timeout is None else float(timeout)
        body = self._payload(messages, stream=True)
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[object]" = asyncio.Queue()

        def worker() -> None:
            try:
                req = urllib.request.Request(
                    f"{self._base}/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers={**self._headers, "Accept": "text/event-stream"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=limit) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            obj = json.loads(chunk)
                            delta = obj["choices"][0]["delta"].get("content")
                        except (ValueError, KeyError, IndexError, TypeError):
                            continue
                        if delta:
                            loop.call_soon_threadsafe(queue.put_nowait, delta)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    err: Exception = ProviderUnavailable(
                        f"provider rejected credentials (HTTP {exc.code})"
                    )
                else:
                    err = LLMError(f"provider HTTP {exc.code}")
                loop.call_soon_threadsafe(queue.put_nowait, err)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, ProviderUnavailable(f"provider unreachable: {exc}")
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        threading.Thread(target=worker, name="aura-llm-stream", daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item  # type: ignore[misc]
        finally:
            # Consumer cancelled: the daemon thread winds down on its own.
            pass
