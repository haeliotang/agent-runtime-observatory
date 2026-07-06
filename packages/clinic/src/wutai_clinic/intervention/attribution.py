from __future__ import annotations

from typing import Any

from wutai_clinic.schemas import InterventionResult

MAIN_LABELS = {
    "intervention_only_resolved_trigger_hit_candidate",
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
    "control_only_resolved_trigger_hit_negative_candidate",
}
SECONDARY_LABEL = "secondary_audit_no_treatment_attribution"


def bool_count(rows: list[dict[str, Any]], field: str, value: bool) -> int:
    return sum(row.get(field) is value for row in rows)


def label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get("effect_label"))
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def classify_pair_summary(row: dict[str, Any]) -> str:
    if (
        row.get("pair_eval_scope") == "main_treatment_attribution_candidate"
        and row.get("intervention_injected_once") is True
        and row.get("main_attribution_eligible") is True
        and str(row.get("effect_label")) in MAIN_LABELS
    ):
        return "main_treatment"
    if (
        row.get("pair_eval_scope") == "secondary_trigger_miss_audit_only"
        and row.get("intervention_injected_once") is False
        and row.get("intervention_treatment_status") == "not_treated_trigger_miss"
        and row.get("effect_label") == SECONDARY_LABEL
    ):
        return "trigger_miss"
    return "invalid"


def split_pair_rows(
    pair_summary: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    main = [
        row
        for row in pair_summary
        if row.get("pair_eval_scope") == "main_treatment_attribution_candidate"
    ]
    secondary = [
        row
        for row in pair_summary
        if row.get("pair_eval_scope") == "secondary_trigger_miss_audit_only"
    ]
    return main, secondary


def outcome_summary(pair_summary: list[dict[str, Any]]) -> dict[str, Any]:
    main, secondary = split_pair_rows(pair_summary)
    main_control_resolved = bool_count(main, "control_resolved", True)
    main_intervention_resolved = bool_count(main, "intervention_resolved", True)
    main_pair_count = len(main)
    return {
        "total_pair_count": len(pair_summary),
        "main_pair_count": main_pair_count,
        "secondary_pair_count": len(secondary),
        "main_pair_ids": [str(row["pair_id"]) for row in main],
        "secondary_pair_ids": [str(row["pair_id"]) for row in secondary],
        "main_effect_label_counts": label_counts(main),
        "secondary_effect_label_counts": label_counts(secondary),
        "main_control_resolved_count": main_control_resolved,
        "main_intervention_resolved_count": main_intervention_resolved,
        "main_resolved_delta": main_intervention_resolved - main_control_resolved,
        "main_control_success_rate": main_control_resolved / main_pair_count
        if main_pair_count
        else None,
        "main_intervention_success_rate": (
            main_intervention_resolved / main_pair_count if main_pair_count else None
        ),
        "secondary_control_resolved_count": bool_count(secondary, "control_resolved", True),
        "secondary_intervention_resolved_count": bool_count(
            secondary, "intervention_resolved", True
        ),
        "trigger_hit_pair_rate_in_batch": main_pair_count / len(pair_summary)
        if pair_summary
        else None,
    }


def continuation_policy(summary: dict[str, Any]) -> dict[str, Any]:
    main_count = int(summary["main_pair_count"])
    positive_count = int(
        summary["main_effect_label_counts"].get(
            "intervention_only_resolved_trigger_hit_candidate", 0
        )
    )
    negative_count = int(
        summary["main_effect_label_counts"].get(
            "control_only_resolved_trigger_hit_negative_candidate", 0
        )
    )
    no_uplift_count = main_count - positive_count - negative_count
    trigger_hit_rate = summary["trigger_hit_pair_rate_in_batch"]
    return {
        "allow_continue_batch2_batchwise": main_count >= 1 and negative_count <= positive_count,
        "allow_full_64_unattended": False,
        "allow_intervention_effect_claim": False,
        "allow_efe_str_predictive_claim": False,
        "allow_trigger_policy_rewrite_before_batch2": False,
        "recommended_next_step": (
            "continue_batch2_batchwise_uncapped_same_frozen_policy"
            if main_count >= 1 and negative_count <= positive_count
            else "pause_before_batch2_manual_review_required"
        ),
        "trigger_policy_note": (
            "Batch-1 trigger coverage is low; keep the frozen policy for batch-2 to avoid "
            "mid-experiment contamination, then review cumulative trigger-hit rate before "
            "any trigger redesign."
        ),
        "evidence_note": (
            f"Batch-1 has {main_count} trigger-hit main pairs, {positive_count} positive, "
            f"{no_uplift_count} no-uplift, {negative_count} negative, "
            f"trigger_hit_rate={trigger_hit_rate}."
        ),
    }


def attribute_pair_summaries(pair_summary: list[dict[str, Any]]) -> dict[str, Any]:
    summary = outcome_summary(pair_summary)
    classification_counts = {"main_treatment": 0, "trigger_miss": 0, "invalid": 0}
    for row in pair_summary:
        classification_counts[classify_pair_summary(row)] += 1
    return {
        "classification_counts": classification_counts,
        "completed_pairs": summary["total_pair_count"],
        "main_treatment_pairs": summary["main_pair_count"],
        "trigger_miss_pairs": summary["secondary_pair_count"],
        "trigger_hit_rate": summary["trigger_hit_pair_rate_in_batch"],
        "intervention_success_rate": summary["main_intervention_success_rate"],
        "control_success_rate": summary["main_control_success_rate"],
        "resolved_delta": summary["main_resolved_delta"],
        "outcome_summary": summary,
        "continuation_policy": continuation_policy(summary),
    }


def attribute(results: list[InterventionResult]) -> dict[str, object]:
    complete = [
        item
        for item in results
        if item.control_resolved is not None and item.intervention_resolved is not None
    ]
    trigger_hits = [item for item in complete if item.trigger_hit and item.injection_count == 1]
    delta_values = [item.resolved_delta for item in trigger_hits if item.resolved_delta is not None]
    return {
        "completed_pairs": len(complete),
        "trigger_hit_pairs": len(trigger_hits),
        "trigger_hit_rate": len(trigger_hits) / len(complete) if complete else 0.0,
        "intervention_success_rate": (
            sum(item.intervention_resolved is True for item in trigger_hits) / len(trigger_hits)
            if trigger_hits
            else 0.0
        ),
        "resolved_delta": sum(delta_values) / len(delta_values) if delta_values else 0.0,
    }
