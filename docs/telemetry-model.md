# Telemetry model

The runtime emits telemetry through the `RunHooks` interface
(`packages/runtime/aro_runtime/hooks.py`), so `aro_runtime` itself has no
telemetry dependencies. `aro_telemetry` provides two hook implementations that
the API and worker compose.

## Traces (OpenTelemetry)

One span per run, one child span per step. Step spans are created at step end
with explicit start/end timestamps taken from the StepRecord, so span timing
equals recorded timing.

| Span | Attributes |
|---|---|
| `agent_run` | `aro.run_id`, `aro.task_id`, `aro.agent`, `aro.status`, `aro.steps`, `aro.denials` |
| `step:<tool>` | `aro.run_id`, `aro.step_index`, `aro.tool`, `aro.input_digest`, `aro.output_digest`, `aro.decision`, `aro.rule_id`, `aro.error` |

Putting digests and policy verdicts on spans is the point: a trace backend
becomes a queryable audit surface ("show me all spans where
`aro.decision = deny`"), not just a latency waterfall.

Exporter selection (`aro_telemetry/otel.py`):

| Environment | Behavior |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` set | OTLP/HTTP batch export |
| `ARO_OTEL_CONSOLE=1` | spans printed to stdout |
| neither | spans stay in-process (no-op cost) |

## Metrics (Prometheus)

Exposed at `GET /metrics` on the API (:8000) and on the worker (:9100).
These names are a public contract — the Grafana dashboard in
`infra/grafana/dashboards/` queries them by name.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `aro_runs_total` | counter | `status` | runs finished, by final status |
| `aro_steps_total` | counter | `tool`, `decision` | steps executed or blocked |
| `aro_policy_denials_total` | counter | `rule_id` | steps blocked by policy |
| `aro_review_debt_total` | counter | `rule_id` | steps flagged `needs_review` (executed, but owed a human look) |
| `aro_run_duration_seconds` | histogram | — | wall-clock run duration |
| `aro_queue_retries_total` | counter | — | queue items retried with backoff after a failure |
| `aro_queue_dead_letters_total` | counter | — | queue items dead-lettered after exhausting retries |
| `aro_rate_limited_total` | counter | — | run-creation requests rejected with 429 |
| `aro_attestations_total` | counter | `decision` | human attestations recorded (accept / amend / reject) |
| `aro_review_debt_open` | gauge | `rule_id` | review-debt items currently open, derived from store state at scrape time |
| `aro_review_debt_cleared` | gauge | `rule_id` | items currently cleared by a valid attestation (falls when a run is overwritten) |
| `aro_review_debt_stale` | gauge | `rule_id` | open items whose only naming attestation is digest-stale |
| `aro_review_debt_oldest_open_age_seconds` | gauge | — | age of the oldest open item (0 if none) |

`aro_review_debt_total` (a counter) records debt *creation* per step. Debt
*consumption* is not a counter — it is the store-derived gauge family above,
set by the api's `/metrics` handler from actual state at scrape time. This is
deliberate: a monotonic counter double-counts under concurrent clears and can
never be undone, so it cannot represent debt that *reopens* when a run is
overwritten. `aro_review_debt_open` is outstanding debt directly (query the api
job); `aro_review_debt_stale` surfaces digest drift; SLO #6 in [slo.md](slo.md)
watches these. A healthy deployment trends open to zero via review, not via
loosening rules.

## Logs

The trace file itself (`data/traces/<run_id>.jsonl`) is the structured log of
record: one JSON event per line (`run_start`, `policy_decision`,
`risk_signal`, `evidence`, `artifact`, `step`, `run_end`). Ship it to any
JSONL-speaking pipeline; replay only ever needs the file back.
