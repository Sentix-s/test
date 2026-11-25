"""The interface the agent needs from an LLM backend. ``OllamaClient``
implements it; a llama.cpp or LM Studio backend could be added later."""

from __future__ import annotations

from typing import Any, Iterator, Protocol

from jarvis.ai.types import ChatChunk, Message
from jarvis.core.cancellation import CancelToken


class ChatBackend(Protocol):
    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
        cancel: CancelToken | None = None,
    ) -> Iterator[ChatChunk]: ...
