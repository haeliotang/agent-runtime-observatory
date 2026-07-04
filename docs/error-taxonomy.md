# Error taxonomy

Every way this system says "no" or "broken", what each one means, where it
shows up, and what an operator does about it. The first row is the most
important distinction in the repo: **a policy denial is not an error.**

| Class | What it means | Where it lands | Run status | Metric | Operator action |
|---|---|---|---|---|---|
| **PolicyDeny** | Governance worked: a rule blocked a step before execution | `PolicyDecision(deny)` + `RiskSignal` on the run; step has `error="blocked by policy rule …"` and no output digest | `completed` (run verdict: `blocked`) | `aro_policy_denials_total{rule_id}` | Review the run; if the deny is wrong, change policy — never bypass |
| **ReviewDebt** | Governance flagged but allowed: step ran, a human look is owed | `PolicyDecision(needs_review)` + `RiskSignal` | `completed` (verdict: `review_required`) | `aro_review_debt_total{rule_id}` | Attest the run (`POST /api/runs/{id}/attestations`) — accept, amend, or reject |
| **ToolError** | The agent could not do what the script claims (missing file, bad patch, absent fixture) | `StepRecord.error` with the tool's message | `failed` | `aro_runs_total{status="failed"}` | Fix the example/workspace or the tool; goldens catch regressions |
| **StepRefError** | A step referenced the output of a step that produced none (`${step:N.output}` after a deny/failure) | `StepRecord.error` | `failed` | same as ToolError | Fix script data-flow; usually a script authoring bug |
| **QueueRetry** | Transient failure in the worker path; item sent back with exponential backoff | queue row: `status=pending`, `attempts` incremented, `last_error`, `available_at` in the future | placeholder stays `pending` | `aro_queue_retries_total` | None if it recovers; inspect `last_error` if attempts climb |
| **DeadLetter** | Retries exhausted (`max_attempts`, default 3) | queue row `status=dead` with final error; placeholder run marked `failed` | `failed` | `aro_queue_dead_letters_total` | `GET /api/queue?status=dead`; fix cause, re-enqueue manually |
| **RateLimited** | Client exceeded run-creation budget (`ARO_RATE_LIMIT_PER_MINUTE`) | HTTP 429 on `POST /api/runs`; nothing recorded | — (no run created) | `aro_rate_limited_total` | Client backs off; raise the knob if legitimate |
| **ReplayDivergence** | Recorded reality and re-derived reality disagree: code drift, environment drift, or tampering | `ReplayReport.divergences[]` (field, recorded, replayed) | — (report, not a run) | CI gate (`tests/replay/`) | Treat as an integrity incident; find which of the three causes it is |
| **NotFound / Gone** | Unknown example, run, or missing trace file | HTTP 404 / 410 | — | — | Client-side fix; 410 on a trace means the file was deleted out-of-band |

## Design rules the taxonomy encodes

1. **Denials complete, tool errors fail.** A blocked step proves the
   guardrail works — failing the run would teach agents (and operators) to
   route around policy. An agent that *couldn't do what it claimed* is a
   failed run.
2. **Every failure at the queue boundary lands somewhere visible.** The
   worker's `except` clause is deliberately broad: any exception either
   schedules a retry (with the error on the row) or dead-letters (with the
   error on the row). There is no path where a queued run silently vanishes.
3. **Chaos is a feature.** `ARO_CHAOS_FAIL_ATTEMPTS=N` injects deterministic
   failures so the QueueRetry and DeadLetter rows of this table are
   *demonstrable* (`tests/integration/test_worker_retry.py`), not
   theoretical.
4. **Replay divergence is its own class**, not a flavor of test failure —
   it is the only class that can mean *tampering*, so it gets zero budget
   (see [slo.md](slo.md) #5).
