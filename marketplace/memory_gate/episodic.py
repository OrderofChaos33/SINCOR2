"""Episodic scratchpad — SQLite, purged when the task closes.

Stores raw thoughts, tool outputs, and failed attempts. Never a long-lived
context window. Redis is the production hot path; SQLite is the portable
default so tests and the Flask blueprint run without extra services.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import List, Optional, Sequence

from .types import ScratchStep


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scratch (
    step_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at REAL NOT NULL,
    tokens TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scratch_task ON scratch(task_id);
"""


class EpisodicStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._lock = threading.Lock()
        # Isolated in-memory connections do not share state; keep one conn.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def append(self, step: ScratchStep) -> None:
        tokens = json.dumps(list(step.tokens))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO scratch
                (step_id, task_id, agent_id, kind, content, status, confidence, created_at, tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    step.task_id,
                    step.agent_id,
                    step.kind,
                    step.content,
                    step.status,
                    float(step.confidence),
                    float(step.created_at),
                    tokens,
                ),
            )

    def list_task(self, task_id: str) -> List[ScratchStep]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scratch WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def purge_task(self, task_id: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM scratch WHERE task_id = ?", (task_id,))
            return int(cur.rowcount)

    def count(self, task_id: Optional[str] = None) -> int:
        with self._lock:
            if task_id is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM scratch").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM scratch WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> ScratchStep:
        tokens = tuple(json.loads(row["tokens"]))
        return ScratchStep(
            step_id=row["step_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            kind=row["kind"],
            content=row["content"],
            status=row["status"],
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            tokens=tokens,
        )
