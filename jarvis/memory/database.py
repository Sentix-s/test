"""SQLite connection handling and schema migrations.

One connection shared across threads, guarded by a lock (SQLite in WAL
mode handles our modest write rate easily). Schema changes are added as
new entries in ``MIGRATIONS`` – never edit an applied migration.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1: conversations, messages, facts, audit journal
    """
    CREATE TABLE conversations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    CREATE TABLE messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('system','user','assistant','tool')),
        content         TEXT NOT NULL DEFAULT '',
        tool_calls      TEXT,
        tool_name       TEXT,
        created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    CREATE INDEX idx_messages_conv ON messages(conversation_id, id);
    CREATE TABLE facts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        content     TEXT NOT NULL,
        category    TEXT NOT NULL DEFAULT 'general',
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    CREATE TABLE audit_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        conversation_id INTEGER,
        tool        TEXT NOT NULL,
        arguments   TEXT NOT NULL,
        risk        TEXT NOT NULL,
        decision    TEXT NOT NULL,
        reason      TEXT NOT NULL DEFAULT '',
        ok          INTEGER,
        output      TEXT,
        duration_ms INTEGER,
        undo        TEXT
    );
    CREATE INDEX idx_audit_ts ON audit_log(ts);
    """,
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        with self._lock:
            current = self.schema_version
            for version, sql in enumerate(MIGRATIONS, start=1):
                if version <= current:
                    continue
                log.info("Applying DB migration %d", version)
                self._conn.execute("BEGIN")
                try:
                    for statement in filter(str.strip, sql.split(";")):
                        self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {version}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
