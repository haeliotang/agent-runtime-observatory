"""Prometheus metrics for agent runs.

Metric names are part of the public contract (see docs/telemetry-model.md and
the Grafana dashboard in infra/grafana): rename here and you must rename there.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from aro_runtime import RunHooks
from aro_schema import AgentRun, Decision, PolicyDecision, ReviewDebtItem, StepRecord, utcnow
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

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

# Review-debt state is exposed as GAUGES derived from the store at scrape time,
# not as a fire-and-forget counter. A gauge is race-free under concurrent
# clears (there is no per-request increment to double-count) and can rise again
# when a run is overwritten and its debt reopens — a monotonic counter can do
# neither. Set from the api's /metrics handler via refresh_review_debt_gauges();
# the api sees every run in the shared store, so these are the authoritative
# cross-process view (query the api job; use max() for the age gauge).
REVIEW_DEBT_OPEN = Gauge(
    "aro_review_debt_open", "Review-debt items currently open (unconsumed), by rule", ["rule_id"]
)
REVIEW_DEBT_CLEARED = Gauge(
    "aro_review_debt_cleared",
    "Review-debt items currently cleared by a valid attestation, by rule",
    ["rule_id"],
)
REVIEW_DEBT_STALE = Gauge(
    "aro_review_debt_stale",
    "Open items whose only naming attestation is digest-stale (run overwritten), by rule",
    ["rule_id"],
)
REVIEW_DEBT_OLDEST_OPEN_AGE_SECONDS = Gauge(
    "aro_review_debt_oldest_open_age_seconds",
    "Age of the oldest currently-open review-debt item (0 if none)",
)


def refresh_review_debt_gauges(
    items: list[tuple[ReviewDebtItem, datetime | None]], now: datetime | None = None
) -> None:
    """Set the review-debt gauges from current store state.

    ``items`` pairs every debt item across all runs with its run's finish time
    (the debt's creation instant, used for age). Called at scrape time, so the
    gauges always reflect true current state regardless of concurrency."""
    now = now or utcnow()
    REVIEW_DEBT_OPEN.clear()
    REVIEW_DEBT_CLEARED.clear()
    REVIEW_DEBT_STALE.clear()
    open_c: dict[str, int] = defaultdict(int)
    cleared_c: dict[str, int] = defaultdict(int)
    stale_c: dict[str, int] = defaultdict(int)
    oldest_open: datetime | None = None
    for item, finished_at in items:
        if item.status == "cleared":
            cleared_c[item.rule_id] += 1
            continue
        open_c[item.rule_id] += 1
        if item.stale_attestation:
            stale_c[item.rule_id] += 1
        if finished_at is not None and (oldest_open is None or finished_at < oldest_open):
            oldest_open = finished_at
    for rule_id, n in open_c.items():
        REVIEW_DEBT_OPEN.labels(rule_id=rule_id).set(n)
    for rule_id, n in cleared_c.items():
        REVIEW_DEBT_CLEARED.labels(rule_id=rule_id).set(n)
    for rule_id, n in stale_c.items():
        REVIEW_DEBT_STALE.labels(rule_id=rule_id).set(n)
    REVIEW_DEBT_OLDEST_OPEN_AGE_SECONDS.set(
        (now - oldest_open).total_seconds() if oldest_open is not None else 0.0
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
