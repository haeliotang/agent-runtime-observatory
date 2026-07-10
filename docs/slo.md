# Service level objectives

SLOs for the reference deployment (docker-compose stack). The point at
reference scale is not the absolute numbers — it is that every objective is
*measurable from the exposed metrics*, with the PromQL written down next to
the target, and that two of the objectives are governance SLOs, which is the
part most agent stacks don't have.

## SLIs and targets

| # | Objective | SLI (PromQL) | Target | Window |
|---|---|---|---|---|
| 1 | API availability | success ratio of `/healthz` probes (compose healthcheck; blackbox exporter when external) | 99.9% | 30d |
| 2 | Run success | `sum(rate(aro_runs_total{status="completed"}[1h])) / sum(rate(aro_runs_total[1h]))` | ≥ 99% | 30d |
| 3 | Run latency | `histogram_quantile(0.95, sum(rate(aro_run_duration_seconds_bucket[5m])) by (le))` | p95 < 1s (scripted runtime) | 7d |
| 4 | Queue health | `aro_queue_dead_letters_total` increase | 0 dead letters | 7d |
| 5 | **Replay integrity** | golden replay divergences in CI | **0 — hard gate, not a ratio** | every commit |
| 6 | **Review debt consumption** | `sum(increase(aro_review_debt_total[7d])) - sum(increase(aro_review_debt_cleared_total[7d]))` — actual per-item consumption, not a count comparison | every `needs_review` item cleared by a named attestation within 7d | 7d |

## Error budgets and their consequences

- **#2 Run success (1% budget).** Burn is visible as
  `aro_runs_total{status="failed"}`. Exhausting the budget means tool
  simulations or examples regressed — the golden suite (#5) usually catches
  the cause first.
- **#4 Queue health (zero budget at reference scale).** A dead letter means
  three attempts with exponential backoff failed; each one carries its final
  error on the queue row (`GET /api/queue?status=dead`). At production scale
  this becomes a rate-based budget; at reference scale any dead letter is a
  bug or a deliberate chaos test.
- **#5 Replay integrity (zero budget, absolute).** This is the SLO that makes
  the others trustworthy: if replay can diverge silently, every other number
  is unverifiable. It is enforced as a CI gate (`tests/replay/`), not
  monitored as a ratio — a divergence blocks merge.
- **#6 Review debt (governance SLO).** `needs_review` steps execute; the debt
  is the gap between "the system allowed it" and "a named human looked at
  *this item*". The SLI pairs per-item debt creation (`aro_review_debt_total`)
  with per-item consumption (`aro_review_debt_cleared_total` — incremented
  only when an accept/amend attestation names a specific needs_review
  decision, and only on first clearing). Sum both across all scraped jobs:
  debt is created in whichever process ran the step (api or worker); it is
  cleared in the api process. A healthy deployment trends the difference to
  zero via attestations — never by loosening rules. Per-run open items:
  `GET /api/runs/{id}/review-debt?status=open`.

## Alerting sketch

Reference alert rules (thresholds per the table above):

```yaml
groups:
  - name: aro-slo
    rules:
      - alert: AroDeadLetter
        expr: increase(aro_queue_dead_letters_total[15m]) > 0
        labels: {severity: page}
      - alert: AroRunFailureBudgetBurn
        expr: |
          sum(rate(aro_runs_total{status="failed"}[1h]))
            / sum(rate(aro_runs_total[1h])) > 0.01
        for: 30m
        labels: {severity: warn}
      - alert: AroReviewDebtStale
        expr: |
          sum(increase(aro_review_debt_total[7d]))
            > sum(increase(aro_review_debt_cleared_total[7d]))
        labels: {severity: warn}
```

Wiring these into the compose Prometheus is tracked in the Grafana/alerting
roadmap issue.

## Honest limits

Reference-scale caveats: one API process, one worker, no HA, and #1 is
measured by the compose healthcheck rather than an external prober. The SLO
*structure* — especially #5 and #6 — is the part designed to survive contact
with a real deployment.
