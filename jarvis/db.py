from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(data_dir / "jarvis.db", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, type TEXT, payload TEXT
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT, kind TEXT CHECK(kind IN ('say','agent')),
                    run_at TEXT NULL, recurrence TEXT NULL, created_at TEXT,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
                """
            )

    def add_event(self, ts: str, type: str, payload: dict) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO events(ts, type, payload) VALUES (?, ?, ?)",
                (ts, type, json.dumps(payload, ensure_ascii=False)),
            )
            return cursor.lastrowid

    def events(self, before: int | None = None, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM (SELECT * FROM events WHERE (? IS NULL OR id < ?) "
                "ORDER BY id DESC LIMIT ?) ORDER BY id",
                (before, before, limit),
            ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "type": row["type"], **json.loads(row["payload"])}
            for row in rows
        ]

    def kv_get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def kv_delete(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def add_reminder(
        self,
        text: str,
        kind: str,
        run_at: str | None = None,
        recurrence: str | None = None,
        created_at: str | None = None,
    ) -> int:
        created_at = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO reminders(text, kind, run_at, recurrence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (text, kind, run_at, recurrence, created_at),
            )
            return cursor.lastrowid

    def get_reminder(self, id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def list_reminders(self, status: str | None = "active") -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reminders WHERE (? IS NULL OR status = ?) ORDER BY id",
                (status, status),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_reminder(self, id: int, **fields: Any) -> None:
        allowed = {"text", "kind", "run_at", "recurrence", "status"}
        if not fields:
            return
        if fields.keys() - allowed:
            raise ValueError("Неизвестное поле напоминания")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE reminders SET {assignments} WHERE id = ?",
                (*fields.values(), id),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
