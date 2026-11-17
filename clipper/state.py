"""Small SQLite store: who approved, who is ignored, and which clips are held or sent."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS approved (
                    platform TEXT, channel TEXT, approved_at REAL,
                    PRIMARY KEY (platform, channel));
                CREATE TABLE IF NOT EXISTS ignored (
                    platform TEXT, channel TEXT,
                    PRIMARY KEY (platform, channel));
                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT, channel TEXT, path TEXT, caption TEXT,
                    created REAL, status TEXT);
                """
            )

    def approve(self, platform: str, channel: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO approved VALUES (?, ?, ?)",
                (platform, channel.lower(), time.time()),
            )
            self._db.execute(
                "DELETE FROM ignored WHERE platform=? AND channel=?",
                (platform, channel.lower()),
            )

    def is_approved(self, platform: str, channel: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM approved WHERE platform=? AND channel=?",
                (platform, channel.lower()),
            ).fetchone()
        return row is not None

    def approved_channels(self, platform: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT channel FROM approved WHERE platform=?", (platform,)
            ).fetchall()
        return [r[0] for r in rows]

    def ignore(self, platform: str, channel: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO ignored VALUES (?, ?)",
                (platform, channel.lower()),
            )

    def is_ignored(self, platform: str, channel: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM ignored WHERE platform=? AND channel=?",
                (platform, channel.lower()),
            ).fetchone()
        return row is not None

    def add_clip(self, platform: str, channel: str, path: str, caption: str, status: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO clips (platform, channel, path, caption, created, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (platform, channel.lower(), path, caption, time.time(), status),
            )
            return int(cur.lastrowid)

    def held_clips(self, platform: str, channel: str) -> list[tuple[int, str, str]]:
        with self._lock:
            return self._db.execute(
                "SELECT id, path, caption FROM clips "
                "WHERE platform=? AND channel=? AND status='held' ORDER BY id",
                (platform, channel.lower()),
            ).fetchall()

    def set_status(self, clip_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE clips SET status=? WHERE id=?", (status, clip_id))
