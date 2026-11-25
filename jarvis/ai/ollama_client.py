"""HTTP client for the local Ollama server (https://github.com/ollama/ollama/blob/main/docs/api.md).

Only the endpoints JARVIS needs are wrapped:

* ``GET  /api/version``  – liveness check
* ``GET  /api/tags``     – installed models
* ``GET  /api/ps``       – models currently loaded in (V)RAM
* ``POST /api/chat``     – streaming chat with native tool calling
* ``POST /api/pull``     – download a model with progress
* ``POST /api/generate`` with ``keep_alive: 0`` – unload a model from VRAM
  (used by power-off, ECO profile and the Performance Manager)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterator

import httpx

from jarvis.ai.types import ChatChunk, ChatResult, Message, ToolCall
from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import LLMError, LLMUnavailableError, ModelNotFoundError, TaskCancelled

log = logging.getLogger(__name__)


def _model_matches(wanted: str, installed: str) -> bool:
    """``qwen3`` matches ``qwen3:latest``; tags must otherwise match exactly."""
    if ":" not in wanted:
        wanted += ":latest"
    if ":" not in installed:
        installed += ":latest"
    return wanted == installed


class OllamaClient:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        request_timeout: float = 180.0,
        connect_timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        timeout = httpx.Timeout(request_timeout, connect=connect_timeout)
        # trust_env=False: never route localhost LLM traffic through a system proxy.
        self._http = httpx.Client(base_url=self.host, timeout=timeout, transport=transport, trust_env=False)

    # ------------------------------------------------------------------ basics
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Ollama не отвечает по адресу {self.host}. Запустите Ollama (значок в трее или `ollama serve`)."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama: превышено время ожидания ({path})") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama: ошибка соединения: {exc}") from exc
        self._raise_for_status(resp)
        return resp

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("error", resp.text))
        except (ValueError, AttributeError):
            return resp.text

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        if not resp.is_stream_consumed:
            resp.read()
        text = self._error_text(resp)
        if resp.status_code == 404 and "not found" in text.lower():
            raise ModelNotFoundError(text)
        raise LLMError(f"Ollama HTTP {resp.status_code}: {text}")

    # ---------------------------------------------------------------- queries
    def version(self) -> str:
        return str(self._request("GET", "/api/version").json().get("version", "unknown"))

    def is_available(self) -> bool:
        try:
            self.version()
            return True
        except LLMError:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/api/tags").json().get("models", []))

    def has_model(self, name: str) -> bool:
        return any(_model_matches(name, m.get("name", "")) for m in self.list_models())

    def loaded_models(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/api/ps").json().get("models", []))

    # ------------------------------------------------------------- lifecycle
    def load(self, model: str, keep_alive: str = "30m") -> None:
        """Load a model into memory without generating anything."""
        self._request("POST", "/api/generate", json={"model": model, "keep_alive": keep_alive})

    def unload(self, model: str) -> None:
        """Free the model's VRAM immediately."""
        self._request("POST", "/api/generate", json={"model": model, "keep_alive": 0})

    def pull(self, model: str, on_progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        try:
            with self._http.stream("POST", "/api/pull", json={"model": model, "stream": True},
                                   timeout=httpx.Timeout(None, connect=5.0)) as resp:
                self._raise_for_status(resp)
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise LLMError(f"Pull failed: {data['error']}")
                    if on_progress:
                        on_progress(data)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"Ollama не отвечает по адресу {self.host}") from exc

    # ------------------------------------------------------------------ chat
    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
        cancel: CancelToken | None = None,
    ) -> Iterator[ChatChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_ollama() for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if think is not None:
            payload["think"] = think

        try:
            with self._http.stream("POST", "/api/chat", json=payload) as resp:
                if cancel is not None:
                    cancel.on_cancel(resp.close)
                self._raise_for_status(resp)
                for line in resp.iter_lines():
                    if cancel is not None:
                        cancel.raise_if_cancelled()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Bad JSON line from Ollama: %r", line[:200])
                        continue
                    if "error" in data:
                        raise LLMError(f"Ollama: {data['error']}")
                    msg = data.get("message") or {}
                    chunk = ChatChunk(
                        content=msg.get("content") or "",
                        thinking=msg.get("thinking") or "",
                        tool_calls=[ToolCall.from_ollama(tc) for tc in msg.get("tool_calls") or []],
                        done=bool(data.get("done")),
                    )
                    if chunk.done:
                        chunk.stats = {k: v for k, v in data.items() if k not in ("message", "done")}
                    yield chunk
                    if chunk.done:
                        return
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Ollama не отвечает по адресу {self.host}. Запустите Ollama (значок в трее или `ollama serve`)."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("Ollama: превышено время ожидания ответа модели") from exc
        except (httpx.ReadError, httpx.StreamClosed, httpx.RemoteProtocolError) as exc:
            if cancel is not None and cancel.cancelled:
                raise TaskCancelled(cancel.reason) from exc
            raise LLMError(f"Ollama: соединение прервано: {exc}") from exc
        if cancel is not None:
            cancel.raise_if_cancelled()

    def chat(self, model: str, messages: list[Message], **kwargs: Any) -> ChatResult:
        """Non-incremental helper that collects a full streamed response."""
        content, thinking, calls, stats = [], [], [], {}
        for chunk in self.chat_stream(model, messages, **kwargs):
            content.append(chunk.content)
            thinking.append(chunk.thinking)
            calls.extend(chunk.tool_calls)
            if chunk.done:
                stats = chunk.stats
        return ChatResult("".join(content), calls, "".join(thinking), stats)
