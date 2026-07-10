# Evidence matrix

The README ends with a strong claim: *"every claim in this README is enforced by
a test you can run."* This document makes that claim auditable. Every load-bearing
statement below is mapped to exactly one of:

- **command** — a shell command that demonstrates it locally;
- **CI job** — a job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) that gates it on every push (8 jobs: `clinic`, `lint`, `unit`, `integration`, `postgres`, `golden-replay`, `web-build`, `compose-e2e`);
- **file / artifact** — where it lives;
- **not shipped** — explicitly, with the tracking issue.

Status legend: ✅ shipped & verifiable · ⚠️ shipped but bounded (read the note) · ❌ not shipped (said so).

## Core claims

| # | Claim (README) | Substantiated by | Status |
|---|---|---|---|
| 1 | Every step is **recorded** with content digests (JSONL trace) | `packages/runtime/aro_runtime/trace.py` (`TRACE_VERSION`, one JSON event/line); a run writes `data/traces/<run_id>.jsonl`; test `tests/unit/test_executor.py::test_trace_roundtrip` (CI: `unit`) | ✅ |
| 2 | Every step is **gated** by declarative policy; a denial is a first-class object, not a log line | `PolicyDecision` in `packages/schema/aro_schema/models.py`; engine `aro_runtime/policy.py`; tests `tests/unit/test_policy.py`, `test_executor.py::test_denied_step_does_not_fail_run` (CI: `unit`) | ✅ |
| 3 | Every trace is **replayable**; replay diffs recorded vs. re-derived reality digest-by-digest | `aro_runtime/replay.py`; `POST /api/runs/{id}/replay` → `{"ok": true, ... "divergences": []}`; tests `tests/replay/test_golden_regression.py` (CI: `golden-replay`) | ✅ |
| 4 | Tampering is **caught** by replay | `tests/replay/test_tamper_detection.py` edits a recorded digest / mutates the workspace and asserts a divergence (CI: `golden-replay`) | ✅ |
| 5 | Every behavior is **measured** — OTel spans + Prometheus metrics | `aro_telemetry/otel.py` (run/step spans, digests + verdicts as attributes; `ARO_OTEL_CONSOLE=1` to see them), `aro_telemetry/metrics.py`; `GET /metrics`; test `tests/integration/test_api.py::test_metrics_exposed` (CI: `integration`) | ✅ |
| 6 | Behavior is **regression-gated** by golden traces in CI | `uv run python -m aro_evals examples` runs fresh, checks `expected.json`, replays the committed golden trace with zero divergence required (CI: `golden-replay`) | ✅ |
| 7 | The Grafana dashboard renders run rate, p95, steps by tool/decision, denials by rule, review debt by rule | `infra/grafana/dashboards/agent-runtime.json` (6 panels); live-data screenshot `docs/assets/grafana-dashboard.png`; the data pipeline feeding it is exercised by the `compose-e2e` CI job (metrics flow + Prometheus scraping both targets) | ✅ pipeline CI-verified; Grafana *panel rendering* itself remains visual-only |

## Object model & governance

| # | Claim | Substantiated by | Status |
|---|---|---|---|
| 8 | Nine first-class objects, accountability structural not aspirational | `packages/schema/aro_schema/models.py`; `docs/object-model.md`; tests `tests/unit/test_schema.py`, `test_alignment_schema.py` (CI: `unit`) | ✅ |
| 9 | `needs_review` executes the step but records **review debt** (`aro_review_debt_total`) | `aro_runtime/executor.py` (records `PolicyDecision(needs_review)` + `RiskSignal`); metric in `aro_telemetry/metrics.py`; run `policy-violation-run` and read `/metrics` | ✅ |
| 10 | Debt is **consumed** by Attestations, per item: `clears_decisions` binds an attestation to specific `needs_review` decisions; `ReviewDebtItem` carries the consumable open/cleared status; reject and run-level attestations clear nothing | `compute_review_debt()` in `aro_schema/review_debt.py`; `GET /api/runs/{id}/review-debt?status=open`; `aro_review_debt_cleared_total`; tests `tests/unit/test_review_debt.py`, `tests/integration/test_review_debt_api.py` (CI: `unit`, `integration`); dashboard Review debt panel with a clear-items form | ✅ |
| 11 | The object model is **field-aligned** with wutai and stillmirror-review | `docs/object-model-alignment.md` (field-by-field mapping + the six implemented deltas: `Attestation`, run `verdict`, `Coverage`, `GoalEvent`, per-step `allocated_to`, `EVIDENCE_ROLES`) | ✅ |
| 12 | `policy-violation-run` = 5-step trace, 3 policy decisions, 3 risk signals, replay zero-divergence | `examples/policy-violation-run/expected.json` asserts exactly this; enforced by `tests/replay/test_golden_regression.py` (CI: `golden-replay`) | ✅ |

## Infrastructure & resilience

| # | Claim | Substantiated by | Status |
|---|---|---|---|
| 13 | Postgres-backed queue with `FOR UPDATE SKIP LOCKED` claims (SQLite fallback) | `aro_runtime/pg_store.py`, `store.py`; `create_store()` selects by `ARO_DATABASE_URL`; test `tests/integration/test_postgres_store.py` against a live Postgres (CI: `postgres` service-container job) | ✅ |
| 14 | Worker retry with exponential backoff, dead-lettering, deterministic chaos injection | `apps/worker/aro_worker/main.py` (`ARO_CHAOS_FAIL_ATTEMPTS`); tests `tests/integration/test_worker_retry.py` (transient→retry→success, persistent→dead-letter) (CI: `integration`) | ✅ |
| 15 | API rate limiting on run creation | `_RateLimiter` in `apps/api/aro_api/main.py` (`ARO_RATE_LIMIT_PER_MINUTE`, 429 + `aro_rate_limited_total`); test `test_api_hardening.py::test_rate_limit_returns_429` (CI: `integration`) | ✅ |
| 16 | Full observability stack runs via `docker compose up --build` | `infra/docker-compose.yml` (api, worker, Postgres, Prometheus, Grafana, healthchecks); exercised end-to-end by the `compose-e2e` CI job — brings the stack up with `--wait`, runs a queued job through the Postgres queue, asserts health / `/metrics` / Prometheus targets | ✅ |

## Discipline & honesty claims

| # | Claim | Substantiated by | Status |
|---|---|---|---|
| 17 | Every failure mode is classified; SLOs incl. two governance SLOs | `docs/error-taxonomy.md` (9 classes; "deny is not an error"), `docs/slo.md` (6 SLOs incl. replay-integrity and review-debt-consumption) | ✅ (docs; the alerting rules they sketch are not yet wired — #8) |
| 18 | Traces are tamper-**evident**, not tamper-**proof** (no signatures) | Stated in README "Failure cases"; replay proves evident (row 4); signing is **not shipped** | ❌ by design — signed work packets are a roadmap item |
| 19 | The agent is a **deterministic scripted runner**; LLM-step recording is not shipped | `aro_runtime/tools.py` (in-process simulated tools); README says so | ❌ by design — roadmap |
| 20 | wutai-clinic: preregistered paired-intervention audit harness, null-reporting, oracle positive control | `packages/clinic/` + its README; CI `clinic` job (`pytest packages/clinic/tests`) | ✅ gated by the `clinic` CI job. The package's internal figures (test count, oracle p-value) are owned by `packages/clinic` and not re-audited in this matrix |

## Known gaps (registered, not hidden)

These are the honest edges. Each is either a tracked issue or an explicit design boundary.

| ID | Gap | Why it matters | Tracking |
|---|---|---|---|
| **G1** | ✅ **Closed** — the docker-compose stack is now exercised in CI | was: verified only by a screenshot, so a stack regression could break silently | closed by the `compose-e2e` job (#9) |
| **G2** | **Demo-grade security defaults** — the API is unauthenticated, CORS is open to `localhost:5173`, and rate limiting is the only guard | The substrate is **not internet-facing**; it is a reference/local deployment. Do not expose `aro_api` publicly without an auth layer in front | boundary stated in [`SECURITY.md`](../SECURITY.md) ✅ |
| **G3** | The exfiltration example ships a **fixture `.env`** (`examples/policy-violation-run/workspace/.env`) | An unlabeled secret-shaped file invites misreading | file now carries a `FAKE` header + redaction note in [`SECURITY.md`](../SECURITY.md) ✅ |
| **G4** | `TRACE_VERSION` exists but there is **no version-reject / migration path** | A future trace-format change would let `load_trace` silently misread an old trace instead of rejecting it | folded into **#12** (OTel/trace-model work) |
| **G5** | ✅ **Closed** — attestations bind to specific `needs_review` decisions (`clears_decisions`); debt items carry a consumable open/cleared status; SLO #6 measures actual per-item consumption | was: run-level attestation + count-comparison SLO | closed by #11 (`review_debt.py`, `/review-debt` endpoint, `aro_review_debt_cleared_total`, dashboard form) |
| **G6** | k8s manifests are **illustrative** (per-pod `emptyDir`, no shared state; image ref not real until GHCR publish) | They demonstrate shape, not a production deployment | issue **#10** |

## Reproduce everything

```bash
uv sync
uv run pytest                          # unit + integration + postgres(skipped w/o DB) + replay
uv run python -m aro_evals examples    # golden-task evals (rows 6, 12)
ARO_OTEL_CONSOLE=1 uv run python -m aro_evals examples   # see OTel spans (row 5)
# full stack (rows 7, 16 — manual, gap G1):
cd infra && docker compose up --build
```

CI runs the same across 8 jobs on every push — including `compose-e2e`, which
brings up the full docker-compose stack and runs this end-to-end smoke.
