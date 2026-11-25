"""Backend-neutral chat data types."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_ollama(self) -> dict[str, Any]:
        return {"function": {"name": self.name, "arguments": self.arguments}}

    @classmethod
    def from_ollama(cls, raw: dict[str, Any]) -> "ToolCall":
        fn = raw.get("function", raw)
        args = fn.get("arguments") or {}
        if isinstance(args, str):  # some models/templates return a JSON string
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_raw": args}
        return cls(name=str(fn.get("name", "")), arguments=args, id=str(raw.get("id") or uuid.uuid4().hex[:12]))


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None  # for role == "tool"

    def to_ollama(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_ollama() for tc in self.tool_calls]
        if self.role == "tool" and self.tool_name:
            msg["tool_name"] = self.tool_name
        return msg


@dataclass
class ChatChunk:
    """One streamed piece of a model response."""

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall]
    thinking: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
