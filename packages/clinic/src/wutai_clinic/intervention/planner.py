from __future__ import annotations

from collections import Counter
from typing import Any

from wutai_clinic.schemas import InterventionArm, InterventionPair, TrajectoryDiagnosis

PACKAGE_VERSION = "phase312_paired_intervention_v1"
INTERVENTION_POLICIES = {
    "insert_validation_checkpoint": {
        "required_reason": "validation_gap_after_edit",
        "trigger_summary": "A file edit/write has been followed by several non-validation steps.",
        "intervention_summary": (
            "Stop further exploration and require a narrow validation step before more "
            "edits, final submission, or broad re-planning."
        ),
    },
    "break_recurrence_and_replan": {
        "required_reason": "loop_or_duplicate_pattern",
        "trigger_summary": "The recent prefix shows repeated structural states or high recurrence.",
        "intervention_summary": (
            "Summarize the current hypothesis, mark the repeated path as exhausted, "
            "and switch to a different evidence source."
        ),
    },
    "error_observation_recovery": {
        "required_reason": "error_streak_or_error_observation",
        "trigger_summary": "The current or recent prefix contains error observations.",
        "intervention_summary": (
            "Parse the latest error class, preserve the failing evidence hash, and "
            "choose a recovery step grounded in that error rather than repeating."
        ),
    },
    "same_action_escape": {
        "required_reason": "same_action_family_streak",
        "trigger_summary": "The same action family has repeated for multiple steps.",
        "intervention_summary": (
            "Block another same-family step unless it introduces new evidence; force "
            "a different action family or a concise re-plan."
        ),
    },
}
ROLE_POLICY_QUOTAS = {
    "failure_target": {
        "insert_validation_checkpoint": 6,
        "break_recurrence_and_replan": 6,
        "error_observation_recovery": 6,
        "same_action_escape": 6,
    },
    "success_sentinel": {
        "insert_validation_checkpoint": 2,
        "break_recurrence_and_replan": 2,
        "error_observation_recovery": 2,
        "same_action_escape": 2,
    },
}
ROLE_OUTCOME = {"failure_target": "failure", "success_sentinel": "success"}
MAX_PER_FAMILY = {"failure_target": 4, "success_sentinel": 2}


def top_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = row.get("top_transition_candidates") or []
    return candidates[0] if candidates else None


def candidate_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for row in rows:
        candidate = top_candidate(row)
        if not candidate:
            continue
        outcome_class = row["outcome_context_for_audit_only"]["outcome_class"]
        for policy_id, policy in INTERVENTION_POLICIES.items():
            if policy["required_reason"] not in candidate["reason_codes"]:
                continue
            records.append(
                {
                    "policy_id": policy_id,
                    "trajectory_id": row["trajectory_id"],
                    "source_task_id": row["source_task_id"],
                    "source_family": row["source_family"],
                    "run_window": row["run_window"],
                    "outcome_class": outcome_class,
                    "prefix_index": candidate["prefix_index"],
                    "prefix_sha256": candidate["prefix_sha256"],
                    "diagnostic_score": candidate["diagnostic_score"],
                    "reason_codes": candidate["reason_codes"],
                    "state_class": candidate["state_class"],
                    "prefix_only_context": candidate["prefix_only_context"],
                }
            )
    return records


def _sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -record["diagnostic_score"],
        record["source_family"],
        record["source_task_id"],
        record["prefix_index"],
        record["policy_id"],
    )


def select_records(
    records: list[dict[str, Any]],
    *,
    role: str,
    policy_id: str,
    quota: int,
    used_tasks: set[str],
    family_counts: Counter[str],
) -> list[dict[str, Any]]:
    outcome = ROLE_OUTCOME[role]
    candidates = sorted(
        (
            record
            for record in records
            if record["outcome_class"] == outcome
            and record["policy_id"] == policy_id
            and record["source_task_id"] not in used_tasks
        ),
        key=_sort_key,
    )
    selected = []
    for record in candidates:
        if len(selected) == quota:
            break
        if family_counts[record["source_family"]] >= MAX_PER_FAMILY[role]:
            continue
        selected.append(record)
        used_tasks.add(record["source_task_id"])
        family_counts[record["source_family"]] += 1
    if len(selected) < quota:
        for record in candidates:
            if len(selected) == quota:
                break
            if record["source_task_id"] in used_tasks:
                continue
            selected.append(record)
            used_tasks.add(record["source_task_id"])
            family_counts[record["source_family"]] += 1
    return selected


def build_pairs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    used_tasks: set[str] = set()
    family_counts_by_role = {role: Counter() for role in ROLE_POLICY_QUOTAS}
    pair_index = 1
    for role, quotas in ROLE_POLICY_QUOTAS.items():
        for policy_id, quota in quotas.items():
            selected = select_records(
                records,
                role=role,
                policy_id=policy_id,
                quota=quota,
                used_tasks=used_tasks,
                family_counts=family_counts_by_role[role],
            )
            for record in selected:
                pair_id = f"phase312_pair_{pair_index:03d}_{role}_{policy_id}"
                pair_index += 1
                policy = INTERVENTION_POLICIES[policy_id]
                pairs.append(
                    {
                        "phase": "3.12",
                        "package_version": PACKAGE_VERSION,
                        "pair_id": pair_id,
                        "selection_role": role,
                        "source_task_id": record["source_task_id"],
                        "source_family": record["source_family"],
                        "source_run_window": record["run_window"],
                        "source_trajectory_id": record["trajectory_id"],
                        "candidate_prefix_index": record["prefix_index"],
                        "candidate_prefix_sha256": record["prefix_sha256"],
                        "candidate_diagnostic_score": record["diagnostic_score"],
                        "candidate_reason_codes": record["reason_codes"],
                        "candidate_state_class": record["state_class"],
                        "candidate_prefix_only_context": record["prefix_only_context"],
                        "intervention_policy_id": policy_id,
                        "intervention_trigger_summary": policy["trigger_summary"],
                        "intervention_summary": policy["intervention_summary"],
                        "selection_basis": (
                            "outcome_stratified_design_plus_prefix_only_audit_candidate; "
                            "future runtime trigger must not read outcome labels"
                        ),
                        "future_eval_target": "official_swebench_resolved_boolean",
                        "status": "package_only_not_run",
                    }
                )
    pairs.sort(key=lambda row: row["pair_id"])
    return pairs


def arm_rows(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for pair in pairs:
        base = {
            "phase": "3.12",
            "package_version": PACKAGE_VERSION,
            "pair_id": pair["pair_id"],
            "selection_role": pair["selection_role"],
            "source_task_id": pair["source_task_id"],
            "source_family": pair["source_family"],
            "source_trajectory_id": pair["source_trajectory_id"],
            "candidate_prefix_sha256": pair["candidate_prefix_sha256"],
            "candidate_prefix_index": pair["candidate_prefix_index"],
            "future_eval_target": pair["future_eval_target"],
            "status": "package_only_not_run",
        }
        rows.append(
            {
                **base,
                "arm_id": f"{pair['pair_id']}__control",
                "arm_type": "control",
                "runtime_policy_id": "frozen_baseline_no_extra_intervention",
                "runtime_policy_summary": (
                    "Rerun the SWE task under the frozen baseline protocol without "
                    "using Phase 3.11 candidate signals."
                ),
            }
        )
        rows.append(
            {
                **base,
                "arm_id": f"{pair['pair_id']}__intervention",
                "arm_type": "intervention",
                "runtime_policy_id": pair["intervention_policy_id"],
                "runtime_policy_summary": pair["intervention_summary"],
                "trigger_signature": {
                    "reason_codes": pair["candidate_reason_codes"],
                    "state_class": pair["candidate_state_class"],
                    "prefix_only_context": pair["candidate_prefix_only_context"],
                },
            }
        )
    return rows


def build_package_rows(
    candidate_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pairs = build_pairs(candidate_records(candidate_rows))
    return pairs, arm_rows(pairs)


def plan(
    diagnoses: list[TrajectoryDiagnosis],
    strategies: list[str],
    n_pairs: int,
) -> list[InterventionPair]:
    pairs: list[InterventionPair] = []
    for index, diagnosis in enumerate(diagnoses[:n_pairs], start=1):
        strategy = (
            strategies[(index - 1) % len(strategies)]
            if strategies
            else "insert_validation_checkpoint"
        )
        trigger = diagnosis.candidates[0].prefix_index if diagnosis.candidates else None
        pair_id = f"clinic_pair_{index:03d}_{strategy}"
        control = InterventionArm(
            pair_id=pair_id,
            arm_type="control",
            source_task=diagnosis.instance_id,
            intervention_policy="frozen_baseline_no_extra_intervention",
        )
        intervention = InterventionArm(
            pair_id=pair_id,
            arm_type="intervention",
            source_task=diagnosis.instance_id,
            intervention_policy=strategy,
            trigger_index=trigger,
            declared_efe_mode="enabled_intervention_trigger_candidate",
        )
        pairs.append(InterventionPair(pair_id=pair_id, control=control, intervention=intervention))
    return pairs
