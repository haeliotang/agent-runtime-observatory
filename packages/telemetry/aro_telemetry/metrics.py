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
