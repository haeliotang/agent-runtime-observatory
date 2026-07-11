"""Postgres-backed run store — same contract as the SQLite RunStore.

The queue claim uses FOR UPDATE SKIP LOCKED, so multiple workers can poll the
same queue without double-claiming: this is the piece SQLite's single-writer
model cannot give you, and the reason this backend exists.
"""

from __future__ import annotations

from datetime import datetime

import psycopg
from aro_schema import AgentRun, Attestation, RunStatus, Task, utcnow
from psycopg.rows import dict_row

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    example TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    denials INTEGER NOT NULL DEFAULT 0,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    task_json TEXT NOT NULL,
    run_json TEXT NOT NULL,
    trace_path TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    id SERIAL PRIMARY KEY,
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


class PostgresRunStore:
    def __init__(self, database_url: str):
        self._conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
        self._conn.execute(_SCHEMA)

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
        self._conn.execute(
            "INSERT INTO runs (id, created_at, status, example, steps, denials, duration_ms,"
            " task_json, run_json, trace_path) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET created_at = EXCLUDED.created_at,"
            " status = EXCLUDED.status, example = EXCLUDED.example, steps = EXCLUDED.steps,"
            " denials = EXCLUDED.denials, duration_ms = EXCLUDED.duration_ms,"
            " task_json = EXCLUDED.task_json, run_json = EXCLUDED.run_json,"
            " trace_path = EXCLUDED.trace_path",
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

    def save_placeholder(self, run_id: str, task: Task, example: str) -> None:
        run = AgentRun(id=run_id, task_id=task.id, agent="scripted@0.1", status=RunStatus.PENDING)
        self.save_run(run, task, example=example)

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run": AgentRun.model_validate_json(row["run_json"]),
            "task": Task.model_validate_json(row["task_json"]),
            "example": row["example"],
            "trace_path": row["trace_path"],
        }

    def list_runs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, status, example, steps, denials, duration_ms "
            "FROM runs ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def all_runs(self) -> list[AgentRun]:
        rows = self._conn.execute("SELECT run_json FROM runs").fetchall()
        return [AgentRun.model_validate_json(row["run_json"]) for row in rows]

    def enqueue(self, run_id: str, example: str) -> int:
        row = self._conn.execute(
            "INSERT INTO queue (run_id, example, status, attempts, enqueued_at) "
            "VALUES (%s, %s, 'pending', 0, %s) RETURNING id",
            (run_id, example, utcnow().isoformat()),
        ).fetchone()
        return row["id"]

    def claim_next(self) -> dict | None:
        now = utcnow().isoformat()
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' "
                "AND (available_at IS NULL OR available_at <= %s) "
                "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (now,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("UPDATE queue SET status = 'running' WHERE id = %s", (row["id"],))
            return dict(row)

    def mark(self, queue_id: int, status: str) -> None:
        self._conn.execute("UPDATE queue SET status = %s WHERE id = %s", (status, queue_id))

    def retry(self, queue_id: int, attempts: int, available_at: datetime, error: str) -> None:
        self._conn.execute(
            "UPDATE queue SET status = 'pending', attempts = %s, available_at = %s, "
            "last_error = %s WHERE id = %s",
            (attempts, available_at.isoformat(), error, queue_id),
        )

    def mark_dead(self, queue_id: int, attempts: int, error: str) -> None:
        self._conn.execute(
            "UPDATE queue SET status = 'dead', attempts = %s, last_error = %s WHERE id = %s",
            (attempts, error, queue_id),
        )

    def list_queue(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM queue WHERE status = %s ORDER BY id DESC LIMIT 100", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM queue ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def save_attestation(self, attestation: Attestation) -> None:
        self._conn.execute(
            "INSERT INTO attestations (id, run_id, created_at, payload) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload",
            (
                attestation.id,
                attestation.run_id,
                utcnow().isoformat(),
                attestation.model_dump_json(),
            ),
        )

    def list_attestations(self, run_id: str) -> list[Attestation]:
        rows = self._conn.execute(
            "SELECT payload FROM attestations WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [Attestation.model_validate_json(row["payload"]) for row in rows]

    def close(self) -> None:
        self._conn.close()
