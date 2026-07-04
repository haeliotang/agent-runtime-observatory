"""SQLite-backed run store and work queue.

Deliberately boring: one file, stdlib sqlite3, denormalized summary columns
for cheap listing. Redis/Postgres are a roadmap item, not a v0.1 need.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from aro_schema import AgentRun, RunStatus, Task, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    example TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    denials INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    task_json TEXT NOT NULL,
    run_json TEXT NOT NULL,
    trace_path TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    example TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    enqueued_at TEXT NOT NULL
);
"""


class RunStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def save_run(
        self,
        run: AgentRun,
        task: Task,
        *,
        example: str | None = None,
        trace_path: str | None = None,
    ) -> None:
        denials = sum(1 for d in run.policy_decisions if d.decision.value == "deny")
        duration_ms = 0.0
        if run.started_at and run.finished_at:
            duration_ms = (run.finished_at - run.started_at).total_seconds() * 1000
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(id, created_at, status, example, steps, denials, duration_ms,"
                " task_json, run_json, trace_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    utcnow().isoformat(),
                    run.status.value,
                    example,
                    len(run.steps),
                    denials,
                    duration_ms,
                    task.model_dump_json(),
                    run.model_dump_json(),
                    trace_path,
                ),
            )
            self._conn.commit()

    def save_placeholder(self, run_id: str, task: Task, example: str) -> None:
        run = AgentRun(id=run_id, task_id=task.id, agent="scripted@0.1", status=RunStatus.PENDING)
        self.save_run(run, task, example=example)

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run": AgentRun.model_validate_json(row["run_json"]),
            "task": Task.model_validate_json(row["task_json"]),
            "example": row["example"],
            "trace_path": row["trace_path"],
        }

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, status, example, steps, denials, duration_ms "
                "FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def enqueue(self, run_id: str, example: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO queue (run_id, example, status, enqueued_at) "
                "VALUES (?, ?, 'pending', ?)",
                (run_id, example, utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid

    def claim_next(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("UPDATE queue SET status = 'running' WHERE id = ?", (row["id"],))
            self._conn.commit()
            return dict(row)

    def mark(self, queue_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE queue SET status = ? WHERE id = ?", (status, queue_id))
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
