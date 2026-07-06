# agent-runtime-observatory

**A reference implementation for tracing, replaying, evaluating, and governing agent runs across trust boundaries.**

[![CI](https://github.com/haeliotang/agent-runtime-observatory/actions/workflows/ci.yml/badge.svg)](https://github.com/haeliotang/agent-runtime-observatory/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## The problem

Agent systems fail differently from services. A broken service returns
errors; a broken agent keeps succeeding at the wrong thing. Yet most agent
stacks record less about their runs than a payments system records about a
$3 refund: unstructured logs, no replay, policy expressed as prompt text,
and post-incident review done by re-reading transcripts and guessing.

This repo is a small, complete answer to a specific question: **what is the
minimum substrate an agent runtime needs so that every claim about a run is
checkable?** The answer implemented here:

1. every step is **recorded** with content digests (JSONL trace);
2. every step is **gated** by declarative policy, and every verdict is a
   first-class object — a denial is data, not a log line;
3. every trace is **replayable**, and replay diffs recorded reality against
   re-derived reality, digest by digest;
4. every behavior is **measured** (OTel spans, Prometheus metrics, Grafana
   dashboard) and **regression-gated** (golden traces in CI).

## System overview

```mermaid
flowchart LR
    SCRIPT[script.json + policy.yaml + workspace/] --> RT[Runtime<br/>execute · gate · record]
    RT -->|trace.jsonl| REPLAY[Replay engine<br/>diff vs. recorded]
    RT -->|hooks| TEL[OTel spans + Prometheus]
    RT --> API[FastAPI + worker]
    API --> WEB[React dashboard]
    TEL --> GRAF[Grafana]
    REPLAY --> EVAL[Golden-task evals<br/>CI regression gate]
```

Full details: [docs/architecture.md](docs/architecture.md).

## Five-minute quickstart

Requires [uv](https://docs.astral.sh/uv/). Node 18+ only if you want the dashboard.

```bash
git clone https://github.com/haeliotang/agent-runtime-observatory.git
cd agent-runtime-observatory
uv sync

# run the full test suite (unit + integration + golden replay regression)
uv run pytest

# run the golden-task evals directly
uv run python -m aro_evals examples

# start the API
uv run uvicorn aro_api.main:app --port 8000
```

Then, in another terminal — execute a run that *attempts credential
exfiltration* and watch policy catch it:

```bash
curl -s -X POST localhost:8000/api/runs \
  -H 'content-type: application/json' \
  -d '{"example": "policy-violation-run"}' | python3 -m json.tool
```

Replay it and verify the record is reproducible:

```bash
RUN_ID=<run_id from above>
curl -s -X POST localhost:8000/api/runs/$RUN_ID/replay | python3 -m json.tool
# → {"ok": true, "steps_compared": 5, "divergences": []}
```

Dashboard: `cd apps/web && npm install && npm run dev` → http://localhost:5173.
Full observability stack (API + worker + Prometheus + Grafana):
`docker compose up --build` from `infra/` — see [infra/README.md](infra/README.md).

## The object model

Nine objects, designed so accountability is structural rather than aspirational
(full doc: [docs/object-model.md](docs/object-model.md)):

| Object | One-line meaning |
|---|---|
| `ReviewerSeat` | the human seat that owns a scope of agent work |
| `Goal` | what was actually asked, with constraints and an owner |
| `Task` | a unit of work derived from a goal |
| `AgentRun` | one execution, carrying all evidence collections |
| `StepRecord` | one gated step: digested input, digested output |
| `PolicyDecision` | allow / deny / needs_review, with rule and reason |
| `RiskSignal` | severity-tagged flag raised by governance |
| `EvidenceItem` | content-addressed pointer a claim can rest on |
| `Artifact` | a produced file, content-addressed |

The load-bearing semantic: **`needs_review` executes the step but records
review debt** — an honest ledger of what a human still owes a look, exposed
as a Prometheus metric (`aro_review_debt_total`). Debt is consumed by
**Attestations** (`POST /api/runs/{id}/attestations`): a named human
accepting, amending, or rejecting a declared scope of the run — with an
explicitly excluded scope, because approval is never total. The object model
is field-aligned with my sibling repos' models; see
[docs/object-model-alignment.md](docs/object-model-alignment.md).

## Trace → replay → eval, concretely

The `policy-violation-run` example is a compromised-agent scenario: read
`app.py` (allowed), read `.env` (**needs_review**), `curl --data @.env` to an
attacker host (**denied**), fetch an unlisted domain (**denied**), write an
incident report (allowed). Running it produces a five-step trace with three
policy decisions and three risk signals; replaying the trace re-executes all
of it and confirms zero divergence; the eval harness asserts exactly this
shape — and CI fails if any of it drifts. Tampering is caught too:
[tests/replay/test_tamper_detection.py](tests/replay/test_tamper_detection.py)
edits a recorded digest and proves replay flags it.

## The observability plane, live

The compose stack (`infra/`) runs API + worker on a Postgres-backed queue,
scraped by Prometheus, rendered by a pre-provisioned Grafana dashboard —
run rate, p95 duration, steps by tool/decision, policy denials by rule, and
review debt by rule:

![Grafana dashboard with live run, denial, and review-debt metrics](docs/assets/grafana-dashboard.png)

Targets and alerting sketches for these panels live in [docs/slo.md](docs/slo.md).

## Failure cases, honestly

- A rule set to `needs_review` does not *stop* anything; if nobody consumes
  the review debt, the risk happened and the system merely proves it. See
  [docs/threat-model.md](docs/threat-model.md) for what is detected vs. prevented.
- Traces are tamper-*evident* (replay catches edits), not tamper-*proof*
  (no signatures yet).
- The agent is a deterministic scripted runner — that is what makes replay
  divergence a hard signal, and it means LLM-step recording is a roadmap
  item, not a shipped feature.
- Every way the system fails is classified in
  [docs/error-taxonomy.md](docs/error-taxonomy.md), with measurable targets in
  [docs/slo.md](docs/slo.md) — including two governance SLOs (replay
  integrity, review-debt consumption) most stacks don't track.

## Relation to my other repos

- [`wutai`](https://github.com/haeliotang/wutai) — a local trust & evidence
  layer for agentic work crossing trust boundaries (signed work packets,
  attention decisions). This repo is the *runtime-side* counterpart: it
  generates the kind of evidence wutai wants to ratify.
- [`stillmirror-review`](https://github.com/haeliotang/stillmirror-review) —
  audits where an agent's attention actually went vs. what was authorized.
  The `ReviewerSeat` / review-debt objects here are the runtime-native
  version of that idea.
- [`coding-agent-intervention-audit`](https://github.com/haeliotang/coding-agent-intervention-audit)
  — runtime-verifiable falsification of intervention claims. The golden
  replay regression in CI is the same discipline applied to this codebase
  itself.

## Roadmap

Tracked as issues; the next increments are:

1. OTel trace-model deepening (OTLP + Tempo in compose, span links to decisions)
2. Counterfactual policy replay — evaluate a *new* policy against *old* traces
3. Grafana review-debt dashboard row + alerting rules (SLO sketch in [docs/slo.md](docs/slo.md))
4. Expanded golden set + nightly regression CI
5. JSON Schema export of the object model
6. ~~Worker scale-out: Postgres queue~~ shipped — Postgres store with
   `SKIP LOCKED` claims, retry/backoff, dead-lettering, chaos injection;
   remaining: k8s worker HPA, GHCR image publish

## Why this matters

Every serious agent platform converges on the same three layers: an
**ontology** (what objects exist and who owns them), an **observability
plane** (what actually happened), and a **governance loop** (what was allowed
and what still needs a human). This repo is those three layers at reference
scale — small enough to read in an afternoon, real enough that every claim in
this README is enforced by a test you can run.

## License

[Apache-2.0](LICENSE)

## Intervention auditing: wutai-clinic

[`packages/clinic`](packages/clinic) — a runtime-verifiable paired-intervention audit
harness for coding agents: preregistration → runtime trigger-hit verification →
paired control/treatment arms → manipulation checks → per-task noise floor (ε) →
official SWE-bench outcome anchoring → null-reporting discipline. Applied honestly,
it has killed every deployable intervention it tested; sensitivity is calibrated via
an oracle positive control (Fisher p=0.0040). See its README for the full protocol.
