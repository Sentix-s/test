"""Repositories on top of :class:`Database`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jarvis.ai.types import Message, ToolCall
from jarvis.memory.database import Database


@dataclass
class ConversationInfo:
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ConversationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, title: str = "") -> int:
        return int(self.db.execute("INSERT INTO conversations(title) VALUES (?)", (title,)).lastrowid)

    def exists(self, conversation_id: int) -> bool:
        return bool(self.db.query("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)))

    def get_title(self, conversation_id: int) -> str:
        rows = self.db.query("SELECT title FROM conversations WHERE id = ?", (conversation_id,))
        return str(rows[0]["title"]) if rows else ""

    def set_title(self, conversation_id: int, title: str) -> None:
        self.db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))

    def add_message(self, conversation_id: int, message: Message) -> int:
        calls = json.dumps([{"name": c.name, "arguments": c.arguments} for c in message.tool_calls],
                           ensure_ascii=False) if message.tool_calls else None
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, tool_calls, tool_name) VALUES (?,?,?,?,?)",
                (conversation_id, message.role, message.content, calls, message.tool_name),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                (conversation_id,),
            )
        return int(cur.lastrowid)

    @staticmethod
    def _row_to_message(row: Any) -> Message:
        calls = [ToolCall(c["name"], c.get("arguments") or {}) for c in json.loads(row["tool_calls"])] \
            if row["tool_calls"] else []
        return Message(role=row["role"], content=row["content"], tool_calls=calls, tool_name=row["tool_name"])

    def messages(self, conversation_id: int, limit: int | None = None) -> list[Message]:
        if limit is None:
            rows = self.db.query("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        else:
            rows = self.db.query(
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (conversation_id, limit),
            )
        return [self._row_to_message(r) for r in rows]

    def list(self, limit: int = 20) -> list[ConversationInfo]:
        rows = self.db.query(
            """SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS n
               FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
               GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC LIMIT ?""",
            (limit,),
        )
        return [ConversationInfo(r["id"], r["title"], r["created_at"], r["updated_at"], r["n"]) for r in rows]

    def latest_id(self) -> int | None:
        rows = self.db.query("SELECT id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1")
        return int(rows[0]["id"]) if rows else None

    def delete(self, conversation_id: int) -> None:
        self.db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


@dataclass
class Fact:
    id: int
    content: str
    category: str
    created_at: str


class FactStore:
    """Long-term facts the user asked JARVIS to remember. Phase 6 adds
    semantic (embedding) search on top of this table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, content: str, category: str = "general") -> int:
        content = content.strip()
        existing = self.db.query("SELECT id FROM facts WHERE content = ?", (content,))
        if existing:
            return int(existing[0]["id"])
        return int(self.db.execute("INSERT INTO facts(content, category) VALUES (?, ?)",
                                   (content, category)).lastrowid)

    def search(self, query: str = "", limit: int = 20) -> list[Fact]:
        words = [w for w in query.lower().split() if len(w) > 1]
        if not words:
            rows = self.db.query("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,))
        else:
            # Python-side matching: SQLite LOWER() is ASCII-only, Russian text needs str.lower().
            rows = [r for r in self.db.query("SELECT * FROM facts ORDER BY id DESC")
                    if any(w in (r["content"] + " " + r["category"]).lower() for w in words)][:limit]
        return [Fact(r["id"], r["content"], r["category"], r["created_at"]) for r in rows]

    def delete(self, fact_id: int) -> bool:
        return self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        risk: str,
        decision: str,
        reason: str = "",
        ok: bool | None = None,
        output: str | None = None,
        duration_ms: int | None = None,
        conversation_id: int | None = None,
        undo: dict[str, Any] | None = None,
    ) -> int:
        return int(self.db.execute(
            """INSERT INTO audit_log(conversation_id, tool, arguments, risk, decision, reason, ok, output,
                                     duration_ms, undo) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (conversation_id, tool, json.dumps(arguments, ensure_ascii=False, default=str), risk, decision,
             reason, None if ok is None else int(ok), (output or "")[:4000], duration_ms,
             json.dumps(undo, ensure_ascii=False) if undo else None),
        ).lastrowid)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
