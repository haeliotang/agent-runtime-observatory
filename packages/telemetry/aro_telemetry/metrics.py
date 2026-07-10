"""Prometheus metrics for agent runs.

Metric names are part of the public contract (see docs/telemetry-model.md and
the Grafana dashboard in infra/grafana): rename here and you must rename there.
"""

from __future__ import annotations

from aro_runtime import RunHooks
from aro_schema import AgentRun, Decision, PolicyDecision, StepRecord
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

RUNS_TOTAL = Counter("aro_runs_total", "Agent runs finished, by final status", ["status"])
STEPS_TOTAL = Counter(
    "aro_steps_total", "Agent steps executed or blocked, by tool and decision", ["tool", "decision"]
)
POLICY_DENIALS_TOTAL = Counter(
    "aro_policy_denials_total", "Steps blocked by policy, by rule", ["rule_id"]
)
REVIEW_DEBT_TOTAL = Counter(
    "aro_review_debt_total",
    "Steps flagged needs_review (executed but owed a human look)",
    ["rule_id"],
)
RUN_DURATION_SECONDS = Histogram(
    "aro_run_duration_seconds",
    "Wall-clock duration of finished runs",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
QUEUE_RETRIES_TOTAL = Counter(
    "aro_queue_retries_total", "Queue items sent back to pending with backoff after a failure"
)
QUEUE_DEAD_LETTERS_TOTAL = Counter(
    "aro_queue_dead_letters_total", "Queue items dead-lettered after exhausting retries"
)
RATE_LIMITED_TOTAL = Counter(
    "aro_rate_limited_total", "Run-creation requests rejected by the API rate limit"
)
ATTESTATIONS_TOTAL = Counter(
    "aro_attestations_total", "Human attestations recorded, by decision", ["decision"]
)
REVIEW_DEBT_CLEARED_TOTAL = Counter(
    "aro_review_debt_cleared_total",
    "Review-debt items cleared by an accept/amend attestation, by rule. "
    "Outstanding debt = sum(aro_review_debt_total) - sum(aro_review_debt_cleared_total) "
    "across all scraped jobs (debt is created in whichever process ran the step; "
    "it is cleared in the api process).",
    ["rule_id"],
)


class MetricsHooks(RunHooks):
    def on_step_end(self, run: AgentRun, step: StepRecord, decision: PolicyDecision | None) -> None:
        label = decision.decision.value if decision else Decision.ALLOW.value
        STEPS_TOTAL.labels(tool=step.name, decision=label).inc()
        if decision and decision.decision == Decision.DENY:
            POLICY_DENIALS_TOTAL.labels(rule_id=decision.rule_id).inc()
        if decision and decision.decision == Decision.NEEDS_REVIEW:
            REVIEW_DEBT_TOTAL.labels(rule_id=decision.rule_id).inc()

    def on_run_end(self, run: AgentRun) -> None:
        RUNS_TOTAL.labels(status=run.status.value).inc()
        if run.started_at and run.finished_at:
            RUN_DURATION_SECONDS.observe((run.finished_at - run.started_at).total_seconds())


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
