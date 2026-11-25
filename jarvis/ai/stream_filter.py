"""Streaming text filter for model output.

* Removes ``<think>...</think>`` blocks (Qwen3 reasoning when an older
  Ollama ignores ``think: false``).
* Stops emitting at ``<tool_call>``: if the chat template failed to turn a
  Qwen tool call into a native ``tool_calls`` field, the raw markup must
  not be shown or spoken; the agent parses it from the full text instead.

Partial tags split across chunks are handled by holding back any suffix
that could be the start of a tag.
"""

from __future__ import annotations

import json
import re

from jarvis.ai.types import ToolCall

_THINK_OPEN, _THINK_CLOSE, _TOOL_OPEN = "<think>", "</think>", "<tool_call>"


class StreamFilter:
    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self.stopped = False  # reached <tool_call>

    @staticmethod
    def _partial_suffix(text: str, tags: tuple[str, ...]) -> int:
        """Length of the longest suffix of ``text`` that is a proper prefix of a tag."""
        best = 0
        for tag in tags:
            for n in range(min(len(tag) - 1, len(text)), 0, -1):
                if text.endswith(tag[:n]):
                    best = max(best, n)
                    break
        return best

    def feed(self, chunk: str) -> str:
        if self.stopped:
            return ""
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            if self._in_think:
                idx = self._buf.find(_THINK_CLOSE)
                if idx < 0:
                    keep = self._partial_suffix(self._buf, (_THINK_CLOSE,))
                    self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                    break
                self._buf = self._buf[idx + len(_THINK_CLOSE):].lstrip("\n")
                self._in_think = False
                continue
            t_idx, c_idx = self._buf.find(_THINK_OPEN), self._buf.find(_TOOL_OPEN)
            candidates = [i for i in (t_idx, c_idx) if i >= 0]
            if not candidates:
                keep = self._partial_suffix(self._buf, (_THINK_OPEN, _TOOL_OPEN))
                emit_to = len(self._buf) - keep
                out.append(self._buf[:emit_to])
                self._buf = self._buf[emit_to:]
                break
            first = min(candidates)
            out.append(self._buf[:first])
            if first == c_idx:
                self.stopped = True
                self._buf = ""
                break
            self._buf = self._buf[first + len(_THINK_OPEN):]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        if self.stopped or self._in_think:
            self._buf = ""
            return ""
        rest, self._buf = self._buf, ""
        return rest


_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.DOTALL)


def parse_text_tool_calls(text: str) -> list[ToolCall]:
    """Extract Qwen/Hermes-style ``<tool_call>{json}</tool_call>`` blocks from plain text."""
    calls = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("name"):
            args = data.get("arguments") or data.get("parameters") or {}
            calls.append(ToolCall.from_ollama({"function": {"name": data["name"], "arguments": args}}))
    return calls


def strip_markup(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.split(_TOOL_OPEN, 1)[0]
    return text.strip()
