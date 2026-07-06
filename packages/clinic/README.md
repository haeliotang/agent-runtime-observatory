# wutai-clinic

`wutai-clinic` is the platform layer of Wutai Observatory. It packages
trajectory diagnosis, intervention, scoring, and evidence-chain logic into
typed schemas, reusable engines, and one CLI.

> **Origin.** Extracted from a private research monorepo. The package is fully
> self-contained (`pip install -e .`, numpy/pyyaml/typer only). Regression
> tests that replay legacy experiment artifacts from the monorepo skip with an
> explicit reason in a standalone checkout (441 passed / 82 such skips);
> nothing in the library code depends on those artifacts. Real SWE-bench
> execution remains optional and guarded; the default install only runs
> offline diagnosis, planning, and audit paths.

## What this is — a runtime-verifiable intervention audit harness

**Wutai Clinic is a runtime-verifiable paired-intervention audit harness for
coding agents.** It does not try to fix agents, and it is not a groundedness /
faithfulness / RAG-quality scorer — that lane (Ragas, DeepEval, TruLens, Phoenix,
LangSmith, Braintrust) is already crowded and well served, and competing there
would add nothing.

It answers a different, less-served question: **did an intervention actually
change the agent's outcome, or is the apparent improvement just leakage, noise, a
benchmark artifact, or a non-reproducible scaffold difference?** A claim only
survives if it passes the full loop:

> preregistration → runtime hook actually fires (trigger-hit) → comparable
> control/treatment arms → manipulation checks → anti-oracle-leakage → measured
> per-task noise floor (ε) → isolated **official SWE-bench outcome** anchoring →
> post-hoc audit / power boundary → and the discipline to **report null**.

Runtime guardrails (Guardrails, NeMo), experiment/trace tooling (LangSmith,
Braintrust, Phoenix, Langfuse), and the SWE-bench harness each ship *pieces* of
this. The contribution here is the **assembled closed loop plus null-reporting
discipline**, which is not yet a standard product form.

**Scope, stated honestly.** The rigor depends on a verifiable ground-truth
outcome, so this applies to *benchmarkable* agent tasks (SWE-bench today), not to
arbitrary production agents that have no outcome oracle. It is a clinical-trial
harness for agent changes, not a universal agent monitor.

### Why the discipline is load-bearing — specificity and sensitivity

A useful audit harness must be both **specific** (it rejects fake improvements)
and **sensitive** (it still detects a real effect when one exists). Wutai Clinic
demonstrates both:

- **Specificity** — applied honestly, it has **killed every deployable intervention
  it tested**: Protocol v0 (4 pairs), v1 (1 fresh pair), and v2 (4 strict-fresh
  pairs) all `both_unresolved_trigger_hit_pair_no_uplift`; the Route-B "deployable
  information" probe (B1: inject issue-derived reproduction) was a **design
  dead-end** — the control agent already receives the issue (reproduction +
  observed error), so the treatment is redundant — and produced no valid uplift
  verdict.
- **Sensitivity** — the one intervention that *does* beat null, **oracle injection**
  (answer-bearing content), is detected as a real positive. It is by construction
  not deployable, so it serves as a **positive control**: proof the harness is not
  permanently emitting null.

> Null results prove the harness is not credulous; the oracle-positive control
> proves it is not blind.

(STR and the trajectory diagnostics remain audit-only and **not predictive** of
failure — claim boundary `audit_only_not_predictive_evidence`, Phase 3.9–3.11
negative.)

The core demo, in one table — the value is not "raise the score", it is to
**prevent you from believing a fake improvement**. This table is generated from
the recorded official-eval reports by `scripts/run_intervention_verdict_demo.py`,
not hand-authored (run with `-o out.json` for per-row source path + SHA256 +
upstream decision; `--check-readme` fails on drift):

<!-- BEGIN generated: intervention-verdicts (run_intervention_verdict_demo.py) -->

| Intervention | Class | Trigger hit | Leakage check | Official outcome | Verdict |
| --- | --- | --- | --- | --- | --- |
| v1 constraint hook (break-loop / require-repro) | behavioral, deployable | yes | clean | 1 pair, 0 uplift | calibrated null |
| v2 constraint hook (break-recurrence + reproduce) | behavioral, deployable | yes | clean | 4 strict-fresh pairs, 0 uplift | calibrated null (underpowered) |
| B1 deployable-info injection (issue-derived reproduction) | informational, deployable | yes (smoke) | redundant w/ control | no valid pair | design dead-end - no verdict |
| oracle / answer-bearing injection (positive control) | informational, NON-deployable | yes | fail (by design) | 1 resolved (1 patch+outcome improved) | true positive - sensitivity anchor |
| oracle-probe sweep (sphinx-8435 / sphinx-8474) | informational, NON-deployable | yes | fail (by design) | 4/4 unmoved | channel bottleneck / capability ceiling |
| epsilon noise floor (control-arm reruns) | calibration | n/a | n/a | 0 flips / 6 reruns | epsilon point 0.00 (95% upper 0.39) |

<!-- END generated: intervention-verdicts -->

Reproduce: `python3 scripts/run_intervention_verdict_demo.py`. **Honest caveat on
sensitivity:** the oracle/positive control moved 1 task but a dedicated oracle
probe failed on 4/4 runs across 2 other tasks (channel bottleneck / capability
ceiling), so "not blind" is an existence proof, not a sensitivity guarantee. All
rows are read from `models/phase6_low_nondeterminism_official_eval_v1/` and
`models/phase6_intervention_mechanism_comparison_report.json`.

### Ledger sync (CLAIMS.md)

The generated table above is conservative; three points from `../CLAIMS.md` sharpen
it without changing any verdict:

- **Sensitivity is stronger than the single-task row suggests (D1, 2026-06-13).** A
  *replay-free* dose-ladder on `pallets__flask-4045` resolved **5/5 at all three
  dose levels** (guidance / detailed / verbatim, each one-sided Fisher p=0.0040),
  calibrating the instrument's uplift-direction sensitivity. So the historical
  deployable-intervention nulls (B1/B3/B6) rest on a *verified-sensitive* measuring
  device, not a blind one. This is the answer-bearing **oracle positive control**:
  non-deployable by construction, and its outcome numbers stay isolated from every
  intervention-effectiveness statistic (D2/B4).
- **ε is per-instance; the pooled row is one axis (C2).** The "0 flips / 6 reruns →
  0.00 (95% upper 0.39)" cell is the *control-arm substrate-unresolved-direction*
  noise floor. It is a distinct measurement from the resolved-direction
  heterogeneity, which **must be reported per instance** and never averaged:
  SWE-bench `sphinx-8474` ε=0/3, `sphinx-8435` ε=1/3 (real non-determinism).
- **Method portability is released (C3, 2026-06-14).** The clinic method was reused
  end-to-end across two heterogeneous runtimes — a Tier-1 48-cell run on the TS
  main-product runtime with DeepSeek `deepseek-v4-pro`, kernel consuming TS
  telemetry with zero source change, M-check gate PASS. This is portability
  evidence only: it still does **not** claim "zero-adaptation to any runtime", and
  the demo outcomes are **not** cognition uplift/harm evidence (B6 unchanged).

## Install

```bash
cd Wutai_observatory/wutai_clinic
pip install -e .
```

Optional groups:

```bash
pip install -e '.[swebench]'
pip install -e '.[mlx]'
pip install -e '.[dev]'
```

## Quick Start

Run the workflow doctor:

```bash
wutai-clinic doctor ../models --json
```

Analyze and diagnose trajectories:

```bash
wutai-clinic analyze ../models/trajectories_purified.jsonl --limit 10 -o /tmp/wutai-analysis.json
wutai-clinic diagnose ../models/trajectories_purified.jsonl --limit 10 -o /tmp/wutai-diagnosis.jsonl
```

Run the five-minute evidence demo:

```bash
python scripts/run_demo.py
```

The demo writes a temporary artifact directory containing:

- `prune_summary.json`
- `analysis_report.json`
- `diagnosis_candidates_10.jsonl`
- `audit_report.json`
- `scorecard.json`
- `closed_loop/closed_loop_evidence_report.json`
- `closed_loop/closed_loop_manifest.json`
- `demo_summary.json`

Generate an intervention dry-run plan:

```bash
wutai-clinic intervene ../models/phase311_trajectory_diagnosis_candidates.jsonl -o /tmp/wutai-arms.jsonl
```

## Current Protocol State

The current Phase 6 evidence stack is outcome-backed but intentionally
underpowered:

- Protocol v0/state-capsule reference evidence has 4 completed official-eval
  pairs, all `both_unresolved_trigger_hit_pair_no_uplift`.
- Protocol v1/constraint-hook evidence has 1 completed fresh official-eval pair:
  `matplotlib__matplotlib-25079`.
- The Protocol v1 label is
  `both_unresolved_trigger_hit_pair_no_uplift`, but the trajectory class is
  `trajectory_diverged_no_uplift`: the hook changed the generated patch path,
  but did not change the official resolved/unresolved outcome.
- The generated Protocol v1 batch report decision is
  `protocol_v1_batch_outcomes_underpowered_no_uplift_observed`.
- The Protocol v2 dry-run gate has passed with decision
  `protocol_v2_dry_run_gate_passed_live_execution_not_authorized`.
- The Protocol v2 pair-input materializer has converted 7 Phase 3.12 candidate
  rows into strict pair-input packages. It materialized 5 rows, marked 5 ready,
  found 4 low-replay-risk rows, and left 2 rows failed because the source action
  was empty. Its decision is `protocol_v2_pair_inputs_batch_ready`.
- The hardened Protocol v2 fresh-candidate gate now selects exactly 4 fresh
  failure targets after excluding completed official-eval rows, duplicates,
  non-failure targets, missing pair inputs, and replay-risk rows. Its decision is
  `protocol_v2_fresh_candidate_set_ready_for_planned_preflight`.
- The 4 fresh Protocol v2 failure targets are `sphinx-doc__sphinx-8474`,
  `sphinx-doc__sphinx-7686`, `sphinx-doc__sphinx-8435`, and
  `pallets__flask-4045`. All 4 are low replay-nondeterminism risk and remain
  unexecuted except for the first target below.
- The first strict Protocol v2 planned preflight is prepared for
  `sphinx-doc__sphinx-8474` with pair id
  `phase312_pair_015_failure_target_error_observation_recovery`, 32 replay
  actions, and 4 mapped constraint-hook actions. Its decision is
  `protocol_v2_planned_preflight_ready_live_execution_not_authorized`.
- The first strict Protocol v2 live control/treatment pair for
  `sphinx-doc__sphinx-8474` has completed isolated official eval. Control and
  treatment both produced patches and both remained unresolved. The treatment
  hook blocked one source-edit attempt and SWE-agent autosubmitted the current
  treatment patch; this is recorded as auditable behavior-control activity, not
  as a positive outcome claim. The outcome-backed label is
  `both_unresolved_trigger_hit_pair_no_uplift`.
- An earlier non-fresh Protocol v2 pair for `sympy__sympy-16281`
  (`phase312_pair_010_failure_target_break_recurrence_and_replan`) also has
  completed isolated official eval with the same label. It predates the
  hardened fresh-candidate gate, so batch aggregation stratifies it as
  `v2_reference`, separate from strict fresh evidence.
- The platform layer now ships `protocol-v2-batch-outcomes`, `power-analysis`,
  `evidence-index`, and `report` commands. The current generated power report
  states that at least 9 strict fresh pairs are required for a powered claim at
  the default design point (target per-pair uplift rate 0.3, trigger-hit rate
  0.6, power 0.8); the current strict fresh pair count is 4.

This means Protocol v2 now has a materialized 4-target fresh-failure batch gate,
plus four completed strict outcome-backed negative/no-uplift pair results.
It does not authorize an unattended run, a batch stability claim, a same-pair
positive attribution claim, a generalized uplift claim, or an EFE/STR predictive
claim. The next evidence-producing step is to add fresh targets and keep running
the same control/treatment/live official-eval path until the powered boundary is
met, or report the underpowered null as the current honest result.

## CLI Reference

```bash
wutai-clinic diagnose INPUT [-o OUTPUT] [--limit N] [--legacy-candidates]
```

Reads trajectory JSONL and writes diagnosis JSONL. With `--legacy-candidates`,
it normalizes existing Phase 3.11 candidate rows.

```bash
wutai-clinic analyze INPUT [-o OUTPUT] [--limit N]
```

Computes the 9 trajectory metrics and corpus aggregate report.

```bash
wutai-clinic prune INPUT -o OUTPUT [--limit N] [--no-dedup] [--rank] [--no-target-hygiene]
```

Applies the Phase 2.6 target hygiene gate by default, then STR loop pruning,
optional deduplication, and optional quality ranking. Use `--no-target-hygiene`
for small local samples where you only want per-trajectory pruning.

```bash
wutai-clinic scorecard INPUT [--eval-suite SUITE] [-o OUTPUT] [--table]
```

Without `--eval-suite`, reads a Phase 3A controlled regression report and emits
the dual scorecard. With `--eval-suite`, scores response rows against expected
routes.

```bash
wutai-clinic intervene INPUT -o OUTPUT [--mode plan|attribute] [--dry-run]
```

In `plan` mode, converts diagnosis candidates to the 64-arm paired intervention
package. In `attribute` mode, summarizes pair-level official eval attribution.
`--real-run` requires `--ack-external`.

```bash
wutai-clinic closed-loop DIAGNOSES PAIR_SUMMARY... -o OUTPUT_DIR \
  --cumulative-report CUMULATIVE_REPORT \
  --trigger-policy-review TRIGGER_REVIEW_REPORT
```

Generates behavior-control closed-loop evidence artifacts: frozen intervention
pairs, arms, attribution report, gated evidence report, and manifest. The
default claim boundary is bounded next-step control evidence, not a generalized
causal-effect or failure-prediction claim. With the Phase 3.16 batch-1 and
batch-2 summaries plus trigger review report, the expected decision is
`closed_loop_trigger_policy_recalibration_required_before_batch3`.

```bash
wutai-clinic protocol-check PROTOCOL CONTROL_CAPSULE TREATMENT_CAPSULE \
  --feature-windows WINDOWS \
  --control-resolved false \
  --treatment-resolved true
```

Validates Task 7 `Protocol v0` and State Capsule fork equivalence before any
single-pair attribution label is allowed. A capsule mismatch returns
`state_mismatch_no_attribution`; positive single-pair labels are conditional on
state equivalence, trigger hit, exactly one injection, and official eval outcome.

`intervention.hybrid_runner` adds the dry-run core for the next execution layer:
`HybridReplayGenerationModel` replays a frozen action prefix before delegating to
the live model, while `CapsuleMaterializationHook` materializes the
pre-intervention capsule at the first generation query and injects Protocol v0
only for treatment arms whose capsule equivalence gate passes. This supports a
capsule-equivalent sequential replay fork, not a process-level Docker snapshot
claim.

```bash
python scripts/run_paired_fork.py --output-dir /tmp/wutai-paired-fork-dry-run
```

Builds a local Task 7 paired-fork dry-run evidence package without Docker,
provider calls, or official eval. It writes protocol, control/treatment
capsules, safe hook/model events, report, and manifest artifacts. Optional mock
outcomes can exercise conditional labels, but the report keeps the official-eval
and generalized-uplift claims disabled.

```bash
wutai-clinic sweagent-preflight -o /tmp/wutai-sweagent-fork-preflight
python scripts/run_sweagent_fork_preflight.py --output-dir /tmp/wutai-sweagent-fork-preflight
```

Builds the SWE-agent adapter preflight evidence package. This validates the
upper-layer adapter contract for sequential replay fork wiring: a read-only
capsule probe, `HybridReplayGenerationModel`, `CapsuleMaterializationHook`, and
Protocol v0 treatment injection gate. The default path intentionally does not
import or start SWE-agent runtime code, Docker, external providers, or official
SWE-bench eval; it is the last offline preflight before a live `RunSingle`
adapter is authorized.

```bash
wutai-clinic sweagent-live-plan
wutai-clinic sweagent-live-plan --ack-docker --ack-external-provider
```

Checks the authorization gate for the next live adapter layer. The live adapter
can attach to an already constructed SWE-agent `RunSingle` object by wrapping
`run_single.agent.model` with `HybridReplayGenerationModel` and adding a
`CapsuleMaterializationHook`. It reads capsule state through
`SWEEnvRuntimeProbe`, which uses `env.deployment.runtime.execute(...)` instead
of the interactive `SWEEnv.communicate(...)` shell session. This command only
emits a readiness report; it does not call `RunSingle.run()`.

```bash
wutai-clinic sweagent-live-single sweagent_run_single.yaml -o /tmp/wutai-sweagent-live-single
wutai-clinic sweagent-live-single sweagent_run_single.yaml -o /tmp/wutai-sweagent-live-single \
  --execute --ack-docker --ack-external-provider
```

Plans or executes one guarded SWE-agent `RunSingle` arm. Plan mode writes the
protocol, replay actions, features, events, report, and manifest without
constructing `RunSingle`. Execute mode lazily loads SWE-agent, constructs
`RunSingle.from_config(...)`, attaches `SWEAgentRunSingleAdapter`, then calls
`run()` only when Docker/provider acknowledgements pass. Treatment execution
also requires `--reference-capsule` so a treatment arm cannot claim attribution
without the control capsule equivalence anchor. A completed live arm archives
the SWE-agent native `.patch`, `.pred`, and `.traj` artifacts into the arm
evidence directory before any later arm can overwrite native output.

```bash
wutai-clinic phase6-official-eval /tmp/wutai-live-preflight \
  -o /tmp/wutai-phase6-official-eval \
  --ack-official-eval
wutai-clinic phase6-official-eval /tmp/wutai-live-preflight \
  -o /tmp/wutai-phase6-official-eval \
  --run-official-eval --ack-official-eval
```

Builds the Phase 6 outcome-backed official-eval package from one completed
Phase 5 live-hook preflight pair. The command reads the archived control and
treatment patches, writes per-arm SWE-bench prediction JSONL files, aggregates
cached official reports when present, and finalizes the pair label only when
both official arm reports are complete. `--run-official-eval` is the only mode
that invokes the SWE-bench harness; without it, the command never starts Docker
or provider calls and remains a package/aggregation step.

```bash
wutai-clinic protocol-v1-fresh-candidates ELIGIBLE_REFS CANDIDATE_POOL_REPORT \
  PROTOCOL_V1_PLAN NO_UPLIFT_DIAGNOSIS \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v1_fresh_candidate_gate
```

Builds the Protocol v1 fresh-candidate gate. It excludes same-pair posthoc
official-eval contamination, keeps success sentinels separate from failure
targets, disables exact static-prefix triggers, and does not authorize Docker,
provider calls, or official eval.

```bash
wutai-clinic protocol-v1-batch-outcomes \
  ../models/phase6_low_nondeterminism_official_eval_v1 \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v1_batch4_outcomes_current
```

Aggregates completed Protocol v1 official-eval outcomes without mixing them
with Protocol v0 state-capsule reference evidence. The current generated report
contains 1 Protocol v1 pair, 0 positive uplift, 1 no-uplift, 0 negative pairs,
and 4 separately stratified v0 reference pairs. Its continuation policy allows
more Protocol v1 pairs and Protocol v2 prescription design, but blocks
stability, intervention-effect, predictive, and unattended full-run claims.

```bash
wutai-clinic protocol-v2-prescription-template \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_prescription_template/protocol_v2_prescription_template.json
```

Emits a guarded Protocol v2 prescription template. The default prescription is
`break_recurrence_and_reproduce`: interrupt repeated failure loops, require
explicit failure reproduction, require an alternative hypothesis before the next
patch, and require targeted post-patch recheck. The schema forbids executable
fields and runtime-visible official eval, resolved/unresolved, or official test
oracles.

```bash
wutai-clinic protocol-v2-dry-run \
  ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v1_batch4_outcomes_current/protocol_v1_batch_outcomes_report.json \
  ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v1_batch4_outcomes_current/protocol_v1_batch_outcomes_pairs.jsonl \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_dry_run_gate \
  --protocol-v2-template ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_prescription_template/protocol_v2_prescription_template.json
```

Converts observed Protocol v1 no-uplift dynamics into a prospective Protocol v2
prescription plan. It only consumes rows with
`trajectory_diverged_no_uplift`, writes a dry-run plan/events/report/manifest,
and explicitly keeps live execution, official eval, same-pair positive
attribution, and official-test/runtime-oracle injection disabled. The current
recommended next step is to materialize new fresh failure targets, then run
Protocol v2 planned preflight on those new targets.

```bash
wutai-clinic protocol-v2-materialize-pair-inputs \
  ../models/phase312_paired_intervention_package/pairs.jsonl \
  --trajectory-root ../software-agent-sdk-main/swe_agent_src/trajectories \
  --output-root ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_state_capsule_pair_inputs \
  --native-root ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_state_capsule_native_runs
```

Materializes strict Protocol v2 pair inputs from Phase 3.12 candidate rows
without constructing `RunSingle`, starting Docker, calling a model provider, or
running official eval. It writes per-task replay actions, run-single configs,
candidate snapshots, reports, and manifests. The current generated package has
7 candidate rows, 5 materialized rows, 5 ready rows, 4 low-replay-risk rows, and
2 failed rows with empty source actions. Its decision is
`protocol_v2_pair_inputs_batch_ready`.

```bash
wutai-clinic protocol-v2-fresh-candidates \
  ../models/phase312_paired_intervention_package/pairs.jsonl \
  ../models/phase6_low_nondeterminism_candidate_pool_eligible_refs.jsonl \
  ../models/phase6_low_nondeterminism_live_candidate_set_candidates.jsonl \
  ../models/phase316_batch03_readiness/batch3_readiness_candidates.jsonl \
  --protocol-v2-dry-run-report ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_dry_run_gate/protocol_v2_dry_run_report.json \
  --official-eval-root ../models \
  --pair-input-root ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_state_capsule_pair_inputs \
  --pair-input-root ../models/phase6_state_capsule_pair_inputs \
  --pair-input-root ../models/phase6_low_nondeterminism_state_capsule_pair_inputs_v2 \
  --pair-input-root ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v1_fresh_state_capsule_pair_inputs \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_candidate_gate \
  --target-pair-count 4
```

Selects Protocol v2 fresh failure targets after excluding contaminated rows:
completed official-eval pairs, success sentinels, duplicates, and known
replay-nondeterministic/state-mismatch cases. The hardened selector also
coalesces duplicate rows before filtering and scans official-eval summaries so
completed historical task/pair rows cannot re-enter as fresh targets. The
current generated evidence package contains 4 fresh failure targets:
`sphinx-doc__sphinx-8474`, `sphinx-doc__sphinx-7686`,
`sphinx-doc__sphinx-8435`, and `pallets__flask-4045`. Its decision is
`protocol_v2_fresh_candidate_set_ready_for_planned_preflight`; it allows
planned preflight but still blocks unattended real-run, stability, and
positive-effect claims.

```bash
wutai-clinic protocol-v2-planned-preflight \
  ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_candidate_gate/protocol_v2_fresh_candidate_set_report.json \
  ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_fresh_candidate_gate/protocol_v2_fresh_candidate_set_candidates.jsonl \
  ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_prescription_template/protocol_v2_prescription_template.json \
  -o ../models/phase6_low_nondeterminism_official_eval_v1/protocol_v2_planned_preflight/sphinx-doc__sphinx-8474 \
  --source-task-id sphinx-doc__sphinx-8474 \
  --model-name MODEL_NAME \
  --api-base https://provider.example/v1
```

Preflights the first Protocol v2 fresh target without constructing `RunSingle`,
starting Docker, calling a model provider, or running official eval. It writes
secret-free control/treatment runtime configs, the selected candidate snapshot,
the hook-action mapping, report, summary, and manifest. The current package has
32 replay actions and maps all 4 Protocol v2 prescription steps; it allows a
future live-single execution only with explicit Docker/provider authorization
and still blocks same-pair positive attribution until paired live arms and
isolated official eval exist.

```bash
wutai-clinic sweagent-protocol-v1-live-single RUN_SINGLE_CONFIG \
  --protocol PROTOCOL_V1_JSON \
  --replay-actions REPLAY_ACTIONS_JSON \
  --arm treatment \
  -o OUTPUT_DIR
wutai-clinic sweagent-protocol-v1-live-single RUN_SINGLE_CONFIG \
  --protocol PROTOCOL_V1_JSON \
  --replay-actions REPLAY_ACTIONS_JSON \
  --arm treatment \
  -o OUTPUT_DIR \
  --execute --ack-docker --ack-external-provider
```

Plans or executes one Protocol v1 live-single arm. Plan mode writes the protocol,
replay actions, events, report, and manifest without constructing `RunSingle`.
Execute mode requires explicit Docker/provider acknowledgements and replays the
frozen prefix before handing control to the live model. Control arms omit the
constraint hook; treatment arms attach the Protocol v1 constraint hook.

```bash
wutai-clinic sweagent-protocol-v1-live-pair CONTROL_DIR TREATMENT_DIR \
  --control-patch CONTROL_PATCH \
  --treatment-patch TREATMENT_PATCH \
  -o PAIR_DIR
wutai-clinic sweagent-protocol-v1-official-eval PAIR_DIR \
  -o OFFICIAL_EVAL_DIR \
  --ack-official-eval
```

Combines two completed Protocol v1 live-single arms into a replay-prefix
equivalent pair and then writes/imports isolated official-eval evidence. Protocol
v1 deliberately does not claim state-capsule equivalence; it claims only that
the replay prefix and hook execution contract were audited before outcome import.

```bash
wutai-clinic sweagent-protocol-v2-live-single RUN_SINGLE_CONFIG \
  --protocol PROTOCOL_V2_JSON \
  --replay-actions REPLAY_ACTIONS_JSON \
  --arm control \
  -o CONTROL_DIR \
  --execute --ack-docker --ack-external-provider
wutai-clinic sweagent-protocol-v2-live-single RUN_SINGLE_CONFIG \
  --protocol PROTOCOL_V2_JSON \
  --replay-actions REPLAY_ACTIONS_JSON \
  --arm treatment \
  -o TREATMENT_DIR \
  --execute --ack-docker --ack-external-provider
```

Plans or executes one Protocol v2 live-single arm. Control arms replay the frozen
prefix and then delegate to the live model without the Protocol v2 hook.
Treatment arms replay the same prefix, then attach `ProtocolV2ConstraintHook`
for post-replay generation. Execute mode strips `wutai_clinic` metadata before
constructing SWE-agent `RunSingleConfig`, requires explicit Docker/provider
acknowledgements, and writes protocol, replay actions, model/hook events,
report, and manifest. It does not run official eval or emit an uplift claim.

```bash
wutai-clinic sweagent-protocol-v2-live-pair CONTROL_DIR TREATMENT_DIR \
  --control-patch CONTROL_PATCH \
  --treatment-patch TREATMENT_PATCH \
  -o PAIR_DIR
wutai-clinic sweagent-protocol-v2-official-eval PAIR_DIR \
  -o OFFICIAL_EVAL_DIR \
  --eval-dir ISOLATED_EVAL_DIR \
  --run-official-eval --ack-official-eval
```

Combines two completed Protocol v2 live-single arms into a replay-prefix
equivalent pair, archives both patches, and then writes/runs isolated official
SWE-bench eval predictions. The current strict `sphinx-doc__sphinx-8474` run
produced `protocol_v2_official_eval_outcome_label_ready` with control
unresolved, treatment unresolved, patch application true for both arms, and
final label `both_unresolved_trigger_hit_pair_no_uplift`. The treatment arm
also recorded an auditable hook-blocked source-edit attempt before autosubmit.
This is a valid negative outcome-backed single-pair result, not a batch
stability result.

```bash
wutai-clinic audit MODELS_DIR
```

Scans legacy reports and manifests into a compact evidence inventory with
artifact SHA256 and JSONL record-count consistency checks.

```bash
wutai-clinic protocol-v2-batch-outcomes EVIDENCE_ROOT -o OUTPUT_DIR \
  [--target-pair-count 4] \
  [--include-v1-reference/--no-v1-reference] \
  [--include-v0-reference/--no-v0-reference]
```

Aggregates completed Protocol v2 official-eval outcomes into separately
stratified layers: strict fresh v2 pairs, non-fresh v2 reference pairs, and
optional v1/v0 reference context. The current generated report contains 1
strict fresh pair and 1 reference pair, 0 uplift pairs, and decision
`protocol_v2_batch_outcomes_underpowered_no_uplift_observed`.

```bash
wutai-clinic power-analysis --pairs N --uplift K --harm H \
  --trigger-hit-rate R --target-uplift-rate D -o OUTPUT_DIR
```

Computes exact paired-outcome power evidence: the maximum per-pair uplift rate
excluded by current results, the number of pairs required for a powered claim
under an assumed trigger-hit rate, and a pre-declared sequential futility
boundary. It quantifies sample-size requirements only; it does not claim any
observed uplift or predictive capability.

```bash
wutai-clinic evidence-index EVIDENCE_ROOT -o OUTPUT_DIR [--table]
```

Scans an evidence root into a machine-readable index of pair-level artifacts
with protocol stratum, effect label, lineage notes, execution status, and
manifest SHA256 consistency. The index records lineage facts only (for example
"not in the fresh candidate gate list"); it makes no claims on behalf of
indexed artifacts.

```bash
wutai-clinic report EVIDENCE_ROOT -o report.html [--analysis ANALYSIS_JSON]
```

Renders existing audited artifacts into a single self-contained HTML file: a
pair-outcome matrix across protocol strata, an evidence DAG, and optional
per-trajectory STR curves. The renderer is read-only and introduces no new
claims.

## Architecture

```text
wutai_clinic/
├── schemas/        # trajectory, diagnosis, scorecard, intervention, evidence dataclasses
├── engine/         # STR, analyzer, grammar gate, pruner, diagnoser, scorer
├── adapters/       # guarded runner/probe contracts and SWE-agent preflight adapter
├── intervention/   # protocols, hooks, runner planning, evaluator, attribution
├── evidence/       # EvidenceChain DAG, standard gates, evidence index
├── io/             # JSONL, report, manifest helpers
├── reporting/      # read-only static HTML evidence report
└── cli.py          # Typer command surface
```

## Evidence Boundary

The package treats trajectory diagnosis and intervention candidates as audit
evidence until paired, trigger-hit official eval supports a stronger claim. It
does not turn STR/diagnosis into a predictive claim by default, and dry-run
commands do not start model calls, Docker, or SWE-bench. Protocol v1 batch
outcomes and Protocol v2 dry-run reports remain bounded evidence: they can guide
the next fresh-failure-target batch, but they cannot be cited as generalized
intervention uplift or predictive diagnosis evidence. Protocol v2 now has a
materialized 4-target fresh-failure candidate gate and four completed strict
official outcome-backed no-uplift pairs. It still does not satisfy a stability,
positive-uplift, or predictive-diagnosis claim until additional fresh targets
complete paired live execution and isolated official eval and the powered
boundary is met.

Bottom line: treat the diagnosis and intervention layers as an audit-and-testbed
**instrument with a known noise floor**, not as evidence that any intervention
works or that STR predicts failure. To date no tested *deployable* intervention
has produced a valid uplift, and the Route-B deployable-information probe (B1) was
redundant-by-design with the control prompt; that null is the current honest
result. The instrument — portability, calibration, preregistration, manipulation
and anti-leakage checks — is the contribution.
