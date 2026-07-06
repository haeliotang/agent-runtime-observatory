# A Runtime-Verifiable Audit Harness for Falsifying Coding-Agent Intervention Claims

*One-page method memo. The verdict table below is generated from recorded
official-eval reports by `scripts/run_intervention_verdict_demo.py` (per-row
source path + SHA256 + upstream decision via `-o out.json`).*

## Problem

Claims that some change makes a coding agent "better" — a new scaffold, a
constraint hook, an injected hint — are routinely accepted on the strength of a
metric bump. But a bump can be **leakage** (the change smuggles in the answer),
**noise** (run-to-run nondeterminism), a **benchmark artifact**, or a
**non-reproducible scaffold difference**. Existing tooling does not test this:
groundedness / faithfulness scorers (Ragas, DeepEval, TruLens) judge answers;
experiment / trace platforms (LangSmith, Braintrust, Phoenix, Langfuse) compare
runs and record traces; the SWE-bench harness supplies reproducible official
outcomes. None of them falsify whether *the intervention itself* changed the
outcome.

## Method

Each intervention runs through one closed loop, and a claim survives only if every
step holds:

> preregistration → runtime hook **trigger-hit** verification → comparable
> **control/treatment** arms → **manipulation checks** → **anti-oracle-leakage** →
> measured per-task **noise floor (ε)** → isolated **official SWE-bench outcome**
> anchoring → post-hoc **audit / power boundary** → the discipline to **report null**.

The components exist in the ecosystem; the contribution is the **assembled loop
plus null-reporting discipline**, which is not a standard product form. Scope is
stated honestly: the rigor depends on a verifiable ground-truth outcome, so it
applies to *benchmarkable* agent tasks (SWE-bench today), not arbitrary production
agents with no outcome oracle.

## Verdict (script-generated)

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

## Specificity and task-conditional sensitivity

- **Specificity (rejects fakes).** Every *deployable* intervention tested came back
  null: Protocol v0 (4 pairs), v1 (1), v2 (4 strict-fresh) all
  `both_unresolved_trigger_hit_pair_no_uplift`; the Route-B deployable-information
  probe (B1) was redundant-by-design with the control prompt and produced no valid
  verdict.
- **Sensitivity (not blind).** The answer-bearing **oracle positive control**
  resolved 1 task — but a dedicated oracle probe **failed on 4/4 runs across 2
  other tasks** (channel bottleneck / capability ceiling). Sensitivity is therefore
  **task-conditional**: an existence proof that the harness *can* detect a real
  effect, not a guarantee that it always will.

> Null results prove the harness is not credulous; the oracle-positive control
> proves it is not blind.

## Limitations

- Requires a ground-truth outcome oracle (SWE-bench today); not a universal agent
  monitor.
- Underpowered: 4 strict-fresh pairs; ≥9 are required for a powered methodological
  claim at the current design point (pooled ε point 0.00, 95% upper 0.39).
- Sensitivity is an existence proof, not measured power.
- STR and trajectory diagnostics are **audit-only, not predictive** of failure
  (claim boundary `audit_only_not_predictive_evidence`; tested negative across
  Phase 3.9–3.11).

## Reproduce

```bash
cd Wutai_observatory/wutai_clinic && pip install -e .
python3 scripts/run_intervention_verdict_demo.py             # the verdict table
python3 scripts/run_intervention_verdict_demo.py -o out.json # + per-row source path, SHA256, upstream decision
python3 scripts/run_intervention_verdict_demo.py --check-readme   # drift guard (CI)
```

Rows are read from `models/phase6_low_nondeterminism_official_eval_v1/` and
`models/phase6_intervention_mechanism_comparison_report.json`.
