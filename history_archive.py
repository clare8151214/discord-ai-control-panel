from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArchiveStats:
    messages: int
    characters: int
    first_message_at: str | None
    last_message_at: str | None


class HistoryArchive:
    """以 SQLite 限期封存房間原始對話；活動上下文仍由 StateStore 管理。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS room_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    author_name TEXT,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE UNIQUE INDEX IF NOT EXISTS room_messages_source_role
                ON room_messages(scope_key, source_message_id, role)
                WHERE source_message_id IS NOT NULL;

                CREATE INDEX IF NOT EXISTS room_messages_scope_id
                ON room_messages(scope_key, id);

                CREATE INDEX IF NOT EXISTS room_messages_created_at
                ON room_messages(created_at);

                CREATE TABLE IF NOT EXISTS archive_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    async def start(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self._initialize_sync)

    def _migrate_scope_sync(
        self,
        scope_key: str,
        entries: list[dict[str, Any]],
    ) -> int:
        metadata_key = f"state-room-history-v1:{scope_key}"
        with closing(self._connect()) as connection, connection:
            migrated = connection.execute(
                "SELECT 1 FROM archive_metadata WHERE key = ?",
                (metadata_key,),
            ).fetchone()
            if migrated is not None:
                return 0

            inserted = 0
            for index, entry in enumerate(entries):
                role = str(entry.get("role", ""))
                content = str(entry.get("content", ""))
                if role not in {"user", "assistant"} or not content:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO room_messages
                        (scope_key, role, content, source_message_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (scope_key, role, content, f"legacy:{index}"),
                )
                inserted += 1

            connection.execute(
                "INSERT OR REPLACE INTO archive_metadata(key, value) VALUES (?, ?)",
                (metadata_key, "complete"),
            )
            return inserted

    async def migrate_scope(
        self,
        scope_key: str,
        entries: list[dict[str, Any]],
    ) -> int:
        async with self.lock:
            return await asyncio.to_thread(
                self._migrate_scope_sync,
                scope_key,
                entries,
            )

    def _append_turn_sync(
        self,
        scope_key: str,
        user_content: str,
        assistant_content: str,
        author_name: str,
        source_message_id: str,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO room_messages
                    (scope_key, role, content, author_name, source_message_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        scope_key,
                        "user",
                        user_content,
                        author_name,
                        source_message_id,
                    ),
                    (
                        scope_key,
                        "assistant",
                        assistant_content,
                        None,
                        source_message_id,
                    ),
                ],
            )

    async def append_turn(
        self,
        scope_key: str,
        user_content: str,
        assistant_content: str,
        author_name: str,
        source_message_id: str,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._append_turn_sync,
                scope_key,
                user_content,
                assistant_content,
                author_name,
                source_message_id,
            )

    def _stats_sync(self, scope_key: str) -> ArchiveStats:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0),
                       MIN(created_at), MAX(created_at)
                FROM room_messages
                WHERE scope_key = ?
                """,
                (scope_key,),
            ).fetchone()
        assert row is not None
        return ArchiveStats(
            messages=int(row[0]),
            characters=int(row[1]),
            first_message_at=row[2],
            last_message_at=row[3],
        )

    async def stats(self, scope_key: str) -> ArchiveStats:
        async with self.lock:
            return await asyncio.to_thread(self._stats_sync, scope_key)

    def _delete_older_than_sync(self, days: int) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                DELETE FROM room_messages
                WHERE created_at < datetime('now', ?)
                """,
                (f"-{days} days",),
            )
            return max(0, int(cursor.rowcount))

    async def delete_older_than(self, days: int) -> int:
        """刪除早於目前 UTC 時間指定天數的訊息，回傳刪除筆數。"""
        if days < 1:
            raise ValueError("SQLite retention days must be at least 1")
        async with self.lock:
            return await asyncio.to_thread(self._delete_older_than_sync, days)

    def _delete_scope_sync(self, scope_key: str) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM room_messages WHERE scope_key = ?",
                (scope_key,),
            )
            connection.execute(
                "DELETE FROM archive_metadata WHERE key = ?",
                (f"state-room-history-v1:{scope_key}",),
            )
            return max(0, int(cursor.rowcount))

    async def delete_scope(self, scope_key: str) -> int:
        async with self.lock:
            return await asyncio.to_thread(self._delete_scope_sync, scope_key)
