"""HTTP surface of the observatory.

Routes live under /api/* so the same paths work in three setups: the Vite dev
server proxy, the FastAPI-served production dashboard, and bare curl.
/metrics and /healthz stay unprefixed for Prometheus and orchestrators.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from aro_runtime import CompositeHooks, Workspace, discover_examples, replay_trace, run_example
from aro_runtime.store import create_store
from aro_schema import (
    Attestation,
    AttestationDecision,
    Decision,
    compute_review_debt,
    run_subject_digest,
    utcnow,
)
from aro_telemetry import MetricsHooks, TracingHooks, render_metrics, setup_tracing
from aro_telemetry.metrics import (
    ATTESTATIONS_TOTAL,
    RATE_LIMITED_TOTAL,
    refresh_review_debt_gauges,
)
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]


class CreateRunRequest(BaseModel):
    example: str
    mode: str = "sync"  # "sync" executes inline; "queued" leaves it for the worker


class CreateAttestationRequest(BaseModel):
    decision: AttestationDecision
    declared_scope: str
    attested_by: str
    excluded_scope: str = ""
    labels: list[str] = []
    proposed_by: str | None = None
    seat_id: str | None = None
    note: str = ""
    # Ids of needs_review PolicyDecisions this attestation clears (see
    # docs/object-model.md — reject cannot clear; unknown ids are rejected).
    clears_decisions: list[str] = []


class _RateLimiter:
    """Fixed-window limiter for run creation. In-process on purpose: one
    honest knob (ARO_RATE_LIMIT_PER_MINUTE), not a distributed system."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._window_start = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            if now - self._window_start >= 60:
                self._window_start = now
                self._count = 0
            self._count += 1
            return self._count <= self.per_minute


def create_app(
    examples_dir: Path | None = None,
    data_dir: Path | None = None,
    database_url: str | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    examples_dir = Path(examples_dir or os.environ.get("ARO_EXAMPLES_DIR", REPO_ROOT / "examples"))
    data_dir = Path(data_dir or os.environ.get("ARO_DATA_DIR", REPO_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    if rate_limit_per_minute is None:
        rate_limit_per_minute = int(os.environ.get("ARO_RATE_LIMIT_PER_MINUTE", "120"))

    setup_tracing("aro-api")
    app = FastAPI(title="agent-runtime-observatory", version="0.2.5")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    store = create_store(database_url=database_url, sqlite_path=data_dir / "aro.db")
    examples = discover_examples(examples_dir)
    hooks = CompositeHooks([MetricsHooks(), TracingHooks()])
    limiter = _RateLimiter(rate_limit_per_minute)

    @app.middleware("http")
    async def rate_limit_runs(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/runs" and not limiter.allow():
            RATE_LIMITED_TOTAL.inc()
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded for run creation"},
            )
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "examples": len(examples)}

    @app.get("/metrics")
    def metrics() -> Response:
        # Derive review-debt gauges from current store state at scrape time
        # (race-free; reflects reopened debt after a run is overwritten).
        items = [
            (item, run.finished_at)
            for run in store.all_runs()
            for item in compute_review_debt(run, store.list_attestations(run.id))
        ]
        refresh_review_debt_gauges(items)
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    @app.get("/api/examples")
    def list_examples() -> list[dict]:
        return [
            {
                "name": ex.name,
                "title": ex.script.task.title,
                "goal": ex.script.goal.statement,
                "steps": len(ex.script.steps),
                "policy_id": ex.policy.id,
            }
            for ex in examples.values()
        ]

    @app.post("/api/runs")
    def create_run(request: CreateRunRequest) -> dict:
        example = examples.get(request.example)
        if example is None:
            raise HTTPException(404, f"unknown example: {request.example}")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        if request.mode == "queued":
            store.save_placeholder(run_id, example.script.task, example.name)
            store.enqueue(run_id, example.name)
            return {"run_id": run_id, "queued": True}
        trace_path = data_dir / "traces" / f"{run_id}.jsonl"
        run = run_example(example, run_id=run_id, hooks=hooks, trace_path=trace_path)
        store.save_run(run, example.script.task, example=example.name, trace_path=str(trace_path))
        return {"run_id": run_id, "queued": False, "run": run.model_dump(mode="json")}

    @app.get("/api/runs")
    def list_runs(limit: int = 50) -> list[dict]:
        return store.list_runs(limit=limit)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        attestations = store.list_attestations(run_id)
        return {
            "run": record["run"].model_dump(mode="json"),
            "task": record["task"].model_dump(mode="json"),
            "example": record["example"],
            "trace_path": record["trace_path"],
            "attestations": [a.model_dump(mode="json") for a in attestations],
            "review_debt": [
                item.model_dump(mode="json")
                for item in compute_review_debt(record["run"], attestations)
            ],
        }

    @app.get("/api/runs/{run_id}/review-debt")
    def get_review_debt(run_id: str, status: str | None = None) -> list[dict]:
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        items = compute_review_debt(record["run"], store.list_attestations(run_id))
        if status:
            items = [item for item in items if item.status == status]
        return [item.model_dump(mode="json") for item in items]

    @app.post("/api/runs/{run_id}/attestations")
    def create_attestation(run_id: str, request: CreateAttestationRequest) -> dict:
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        run = record["run"]

        # Identity is self-declared within the trusted interior (the API is
        # unauthenticated — see SECURITY.md). We can still refuse the things that
        # would make the record a lie: a seat this run never declared, and
        # (below) clearing a specific debt item without naming an accountable seat.
        seat_ids = {seat.id for seat in run.reviewer_seats}
        if request.seat_id is not None and request.seat_id not in seat_ids:
            raise HTTPException(
                422, f"seat_id {request.seat_id!r} is not a reviewer seat of run {run_id}"
            )

        # Dedup within the request so one call cannot double-count a clearing.
        clears = list(dict.fromkeys(request.clears_decisions))
        if clears:
            if request.seat_id is None:
                raise HTTPException(
                    422, "clearing a specific review-debt item requires an accountable seat_id"
                )
            if request.decision == AttestationDecision.REJECT:
                raise HTTPException(422, "a reject attestation cannot clear review debt items")
            reviewable = {d.id for d in run.policy_decisions if d.decision == Decision.NEEDS_REVIEW}
            unknown = [d for d in clears if d not in reviewable]
            if unknown:
                raise HTTPException(
                    422, f"not needs_review decisions of run {run_id}: {', '.join(unknown)}"
                )

        try:
            attestation = Attestation(
                id=f"att-{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                seat_id=request.seat_id,
                decision=request.decision,
                declared_scope=request.declared_scope,
                excluded_scope=request.excluded_scope,
                labels=request.labels,
                proposed_by=request.proposed_by,
                attested_by=request.attested_by,
                note=request.note,
                # Bind the attestation to the exact record being reviewed; if the
                # run is later overwritten, the digest no longer matches and the
                # debt reopens (compute_review_debt flags it stale).
                subject_digest=run_subject_digest(run),
                attested_at=utcnow(),
                clears_decisions=clears,
            )
        except ValidationError as exc:
            raise HTTPException(422, f"invalid attestation: {exc.errors()[0]['msg']}") from exc

        store.save_attestation(attestation)
        ATTESTATIONS_TOTAL.labels(decision=attestation.decision.value).inc()
        # No per-request debt counting: the cleared/open/stale gauges are derived
        # from store state at scrape time (see /metrics), so concurrent clears
        # cannot double-count and reopened debt is reflected automatically.
        return attestation.model_dump(mode="json")

    @app.get("/api/queue")
    def list_queue(status: str | None = None) -> list[dict]:
        return store.list_queue(status=status)

    @app.get("/api/runs/{run_id}/trace")
    def get_trace(run_id: str) -> Response:
        record = store.get_run(run_id)
        if record is None or not record["trace_path"]:
            raise HTTPException(404, f"no trace for run: {run_id}")
        trace_file = Path(record["trace_path"])
        if not trace_file.exists():
            raise HTTPException(410, f"trace file missing: {trace_file}")
        return Response(content=trace_file.read_text(), media_type="application/x-ndjson")

    @app.post("/api/runs/{run_id}/replay")
    def replay_run(run_id: str) -> dict:
        record = store.get_run(run_id)
        if record is None or not record["trace_path"]:
            raise HTTPException(404, f"no trace for run: {run_id}")
        example = examples.get(record["example"])
        if example is None:
            raise HTTPException(410, f"example no longer available: {record['example']}")
        report = replay_trace(Path(record["trace_path"]), Workspace.from_dir(example.workspace_dir))
        return {"ok": report.ok, **report.model_dump(mode="json")}

    web_dist = REPO_ROOT / "apps" / "web" / "dist"
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="dashboard")

    return app


app = create_app()
