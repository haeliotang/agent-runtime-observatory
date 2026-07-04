"""Run stores: SQLite (default) and Postgres (via ARO_DATABASE_URL).

Both back the same three tables — runs (denormalized summaries for cheap
listing), queue (work items with retry/dead-letter semantics), attestations
(humans standing behind runs). The queue contract:

    pending --claim--> running --success--> done
       ^                  |
       |                  +--failure, attempts < max--> pending (available_at
       +--backoff elapses-+                             pushed into the future)
                          +--failure, attempts >= max--> dead   (dead letter)

Timestamps are stored as ISO-8601 UTC strings in both backends so the
available_at comparison is a plain lexical compare.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from aro_schema import AgentRun, Attestation, RunStatus, Task, utcnow

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
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT,
    last_error TEXT,
    enqueued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attestations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""

# Columns added after the first release; applied best-effort so an existing
# local aro.db keeps working without a migration tool.
_SQLITE_MIGRATIONS = (
    "ALTER TABLE queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE queue ADD COLUMN available_at TEXT",
    "ALTER TABLE queue ADD COLUMN last_error TEXT",
)


class RunStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for migration in _SQLITE_MIGRATIONS:
                try:
                    self._conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already exists
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
                "INSERT INTO queue (run_id, example, status, attempts, enqueued_at) "
                "VALUES (?, ?, 'pending', 0, ?)",
                (run_id, example, utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid

    def claim_next(self) -> dict | None:
        now = utcnow().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' "
                "AND (available_at IS NULL OR available_at <= ?) "
                "ORDER BY id LIMIT 1",
                (now,),
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

    def retry(self, queue_id: int, attempts: int, available_at: datetime, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE queue SET status = 'pending', attempts = ?, available_at = ?, "
                "last_error = ? WHERE id = ?",
                (attempts, available_at.isoformat(), error, queue_id),
            )
            self._conn.commit()

    def mark_dead(self, queue_id: int, attempts: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE queue SET status = 'dead', attempts = ?, last_error = ? WHERE id = ?",
                (attempts, error, queue_id),
            )
            self._conn.commit()

    def list_queue(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM queue"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id DESC LIMIT 100"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_attestation(self, attestation: Attestation) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO attestations (id, run_id, created_at, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    attestation.id,
                    attestation.run_id,
                    utcnow().isoformat(),
                    attestation.model_dump_json(),
                ),
            )
            self._conn.commit()

    def list_attestations(self, run_id: str) -> list[Attestation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM attestations WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [Attestation.model_validate_json(row["payload"]) for row in rows]

    def close(self) -> None:
        self._conn.close()


def create_store(database_url: str | None = None, sqlite_path: Path | None = None) -> RunStore:
    """Pick a backend: Postgres when a postgres:// URL is given (or set via
    ARO_DATABASE_URL), SQLite otherwise."""
    url = database_url if database_url is not None else os.environ.get("ARO_DATABASE_URL")
    if url and url.startswith(("postgres://", "postgresql://")):
        from aro_runtime.pg_store import PostgresRunStore

        return PostgresRunStore(url)
    if sqlite_path is None:
        raise ValueError("sqlite_path is required when no Postgres URL is configured")
    return RunStore(sqlite_path)
