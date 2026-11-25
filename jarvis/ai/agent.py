"""The agent loop: model -> tool calls -> permission check -> execution ->
results back to the model -> ... -> final answer.

This is what lets JARVIS do multi-step tasks without hard-coded commands:
the model sees every registered tool's schema and decides which to call.
The loop yields :class:`AgentEvent` objects so the CLI, the GUI and the
voice engine (which speaks text as it streams) can all consume it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

from jarvis.ai.backend import ChatBackend
from jarvis.ai.prompts import build_system_prompt
from jarvis.ai.stream_filter import StreamFilter, parse_text_tool_calls, strip_markup
from jarvis.ai.types import Message, ToolCall
from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import LLMError, TaskCancelled
from jarvis.memory.store import AuditLog, ConversationStore, FactStore
from jarvis.security.permissions import Decision, PermissionManager
from jarvis.settings.schema import AISettings, MemorySettings
from jarvis.tools.base import ToolContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

EventKind = Literal["text", "thinking", "tool_start", "tool_end", "denied", "done", "cancelled", "error"]

MAX_TOOL_OUTPUT_CHARS = 8000


@dataclass
class AgentEvent:
    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Agent:
    def __init__(
        self,
        backend: ChatBackend,
        registry: ToolRegistry,
        permissions: PermissionManager,
        conversations: ConversationStore,
        facts: FactStore,
        audit: AuditLog,
        ai_settings: AISettings,
        memory_settings: MemorySettings,
        model: str | None = None,
        services: dict[str, Any] | None = None,
        system_prompt_extra: Callable[[], str] | None = None,
    ) -> None:
        self.backend = backend
        self.registry = registry
        self.permissions = permissions
        self.conversations = conversations
        self.facts = facts
        self.audit = audit
        self.ai = ai_settings
        self.mem = memory_settings
        self.model = model or ai_settings.model
        self.services = {"facts": facts, "conversations": conversations, **(services or {})}
        self.system_prompt_extra = system_prompt_extra

    # ---------------------------------------------------------------- context
    def _system_message(self) -> Message:
        facts = [f.content for f in self.facts.search(limit=15)]
        extra = self.system_prompt_extra() if self.system_prompt_extra else ""
        return Message("system", build_system_prompt(facts, extra))

    def build_context(self, conversation_id: int) -> list[Message]:
        history = self.conversations.messages(conversation_id, limit=self.mem.max_context_messages)
        # Trim oldest messages to the character budget (cheap proxy for tokens).
        total, start = 0, len(history)
        for i in range(len(history) - 1, -1, -1):
            m = history[i]
            total += len(m.content) + sum(len(str(c.arguments)) for c in m.tool_calls) + 20
            if total > self.ai.history_char_budget and i < len(history) - 1:
                break
            start = i
        history = history[start:]
        # Never start with an orphaned tool result or assistant tool-call turn.
        while history and history[0].role != "user":
            history.pop(0)
        return history

    def _options(self) -> dict[str, Any]:
        return {"temperature": self.ai.temperature, "num_ctx": self.ai.num_ctx}

    def _persist(self, conversation_id: int, msg: Message, history: list[Message]) -> None:
        self.conversations.add_message(conversation_id, msg)
        history.append(msg)

    # ------------------------------------------------------------------ tools
    def _run_tool(self, conversation_id: int, call: ToolCall, cancel: CancelToken) -> Iterator[AgentEvent | ToolResult]:
        tool = self.registry.get(call.name)
        if tool is None:
            result = ToolResult(False, f"Unknown tool '{call.name}'.")
            self.audit.record(tool=call.name, arguments=call.arguments, risk="unknown", decision="deny",
                              reason="unknown tool", ok=False, conversation_id=conversation_id)
            yield result
            return
        decision, reason = self.permissions.check(tool, call.arguments)
        if decision is Decision.DENY:
            self.audit.record(tool=tool.name, arguments=call.arguments, risk=tool.risk.value, decision="deny",
                              reason=reason, conversation_id=conversation_id)
            yield AgentEvent("denied", tool.describe_call(call.arguments), {"tool": tool.name, "reason": reason})
            yield ToolResult(False, f"Permission denied ({reason}). Do not retry this action; tell the user.")
            return
        yield AgentEvent("tool_start", tool.describe_call(call.arguments),
                         {"tool": tool.name, "arguments": call.arguments, "risk": tool.risk.value})
        ctx = ToolContext(cancel=cancel, services=self.services)
        result = self.registry.execute(tool.name, call.arguments, ctx)
        self.audit.record(tool=tool.name, arguments=call.arguments, risk=tool.risk.value, decision="allow",
                          reason=reason, ok=result.ok, output=result.output, duration_ms=result.duration_ms,
                          conversation_id=conversation_id)
        yield AgentEvent("tool_end", result.output[:500],
                         {"tool": tool.name, "ok": result.ok, "duration_ms": result.duration_ms})
        yield result

    # ------------------------------------------------------------------- loop
    def _model_turn(self, messages: list[Message], tools: list[dict[str, Any]] | None,
                    cancel: CancelToken, out: dict[str, Any]) -> Iterator[AgentEvent]:
        filt = StreamFilter()
        parts: list[str] = []
        calls: list[ToolCall] = []
        for chunk in self.backend.chat_stream(self.model, messages, tools=tools, options=self._options(),
                                              keep_alive=self.ai.keep_alive, think=self.ai.think, cancel=cancel):
            if chunk.thinking:
                yield AgentEvent("thinking", chunk.thinking)
            if chunk.content:
                parts.append(chunk.content)
                visible = filt.feed(chunk.content)
                if visible:
                    yield AgentEvent("text", visible)
            calls.extend(chunk.tool_calls)
            if chunk.done:
                out["stats"] = chunk.stats
        tail = filt.flush()
        if tail:
            yield AgentEvent("text", tail)
        full = "".join(parts)
        if not calls and tools:
            calls = parse_text_tool_calls(full)
        out["text"] = strip_markup(full)
        out["calls"] = calls

    def run(self, conversation_id: int, user_text: str, cancel: CancelToken | None = None) -> Iterator[AgentEvent]:
        cancel = cancel or CancelToken()
        history = self.build_context(conversation_id)
        self._persist(conversation_id, Message("user", user_text), history)
        if len(self.conversations.messages(conversation_id, limit=2)) == 1:
            self.conversations.set_title(conversation_id, user_text.strip().splitlines()[0][:80])

        tools = self.registry.schemas(exclude=set(self.permissions.settings.blocked_tools)) or None
        try:
            for _ in range(self.ai.max_tool_iterations):
                cancel.raise_if_cancelled()
                turn: dict[str, Any] = {}
                yield from self._model_turn([self._system_message(), *history], tools, cancel, turn)
                calls: list[ToolCall] = turn["calls"]
                self._persist(conversation_id, Message("assistant", turn["text"], tool_calls=calls), history)
                if not calls:
                    yield AgentEvent("done", turn["text"], {"stats": turn.get("stats", {})})
                    return
                for call in calls:
                    cancel.raise_if_cancelled()
                    result: ToolResult | None = None
                    for item in self._run_tool(conversation_id, call, cancel):
                        if isinstance(item, ToolResult):
                            result = item
                        else:
                            yield item
                    assert result is not None
                    output = result.for_model()
                    if len(output) > MAX_TOOL_OUTPUT_CHARS:
                        output = output[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
                    self._persist(conversation_id, Message("tool", output, tool_name=call.name), history)

            # Step limit reached: ask for a final answer with tools disabled.
            note = Message("user", "[system] Step limit reached. Summarize what was done and what remains, without calling tools.")
            turn = {}
            yield from self._model_turn([self._system_message(), *history, note], None, cancel, turn)
            self._persist(conversation_id, Message("assistant", turn["text"]), history)
            yield AgentEvent("done", turn["text"], {"step_limit": True})
        except TaskCancelled as exc:
            log.info("Task cancelled: %s", exc)
            self.conversations.add_message(conversation_id, Message("assistant", "[задача остановлена пользователем]"))
            yield AgentEvent("cancelled", str(exc))
        except LLMError as exc:
            log.error("LLM error: %s", exc)
            yield AgentEvent("error", str(exc), {"type": type(exc).__name__})
