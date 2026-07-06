from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.attribution import attribute_pair_summaries
from wutai_clinic.intervention.planner import build_package_rows
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

CLOSED_LOOP_VERSION = "phase317_behavior_control_closed_loop_v1"
CLAIM_BOUNDARY = (
    "Closed-loop evidence uses frozen intervention plans, official eval pair summaries, "
    "and optional cumulative trigger-policy review reports. It supports bounded next-step "
    "control decisions, not a generalized causal effect claim or a universal "
    "failure-prediction claim."
)


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl":
        with path.open("rb") as handle:
            record_count = sum(1 for line in handle if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "record_count": record_count,
    }


def closed_loop_gates(
    *,
    pairs: list[dict[str, Any]],
    arms: list[dict[str, Any]],
    pair_summary: list[dict[str, Any]],
    attribution: dict[str, Any],
    cumulative_report: dict[str, Any] | None = None,
    trigger_policy_review: dict[str, Any] | None = None,
) -> dict[str, bool]:
    classification_counts = attribution["classification_counts"]
    gates = {
        "plan_generated": len(pairs) > 0 and len(arms) == len(pairs) * 2,
        "official_eval_pairs_present": len(pair_summary) > 0,
        "official_eval_completed": attribution["completed_pairs"] == len(pair_summary),
        "main_treatment_pairs_present": attribution["main_treatment_pairs"] > 0,
        "trigger_hits_observed": float(attribution["trigger_hit_rate"] or 0.0) > 0.0,
        "attribution_classification_valid": classification_counts["invalid"] == 0,
        "non_negative_resolved_delta": int(attribution["resolved_delta"]) >= 0,
        "claim_boundary_present": bool(CLAIM_BOUNDARY),
    }
    if cumulative_report is not None:
        gates["cumulative_diagnosis_passed"] = cumulative_report.get("passed") is True
        gates["cumulative_pair_count_matches"] = cumulative_report.get(
            "cumulative_summary", {}
        ).get("selected_pair_count") == len(pair_summary)
        gates["predictive_claim_not_made"] = (
            cumulative_report.get("claim_boundary", {}).get("efe_str_predictive_claimed") is False
        )
        gates["paired_uplift_claim_not_made"] = (
            cumulative_report.get("claim_boundary", {}).get("paired_uplift_claimed") is False
        )
    if trigger_policy_review is not None:
        continuation_policy = trigger_policy_review.get("continuation_policy", {})
        gates["trigger_policy_review_passed"] = trigger_policy_review.get("passed") is True
        gates["recalibration_required_before_batch3"] = (
            continuation_policy.get("require_live_trigger_recalibration_protocol_before_batch3")
            is True
        )
        gates["same_static_policy_not_allowed_for_batch3"] = (
            continuation_policy.get("allow_batch3_same_static_prefix_policy") is False
        )
    return gates


def closed_loop_decision(
    gates: dict[str, bool],
    *,
    cumulative_report: dict[str, Any] | None = None,
    trigger_policy_review: dict[str, Any] | None = None,
) -> str:
    if not all(gates.values()):
        return "closed_loop_evidence_blocked"
    if trigger_policy_review is not None:
        return "closed_loop_trigger_policy_recalibration_required_before_batch3"
    if cumulative_report is not None:
        return "closed_loop_cumulative_batchwise_diagnosis_ready"
    return "closed_loop_batchwise_continuation_ready"


def closed_loop_report(
    *,
    candidate_rows: list[dict[str, Any]],
    pair_summary: list[dict[str, Any]],
    cumulative_report: dict[str, Any] | None = None,
    trigger_policy_review: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    pairs, arms = build_package_rows(candidate_rows)
    attribution = attribute_pair_summaries(pair_summary)
    gates = closed_loop_gates(
        pairs=pairs,
        arms=arms,
        pair_summary=pair_summary,
        attribution=attribution,
        cumulative_report=cumulative_report,
        trigger_policy_review=trigger_policy_review,
    )
    decision = closed_loop_decision(
        gates,
        cumulative_report=cumulative_report,
        trigger_policy_review=trigger_policy_review,
    )
    extras: dict[str, Any] = {
        "version": CLOSED_LOOP_VERSION,
        "claim_boundary": CLAIM_BOUNDARY,
        "plan_summary": {
            "pair_count": len(pairs),
            "arm_count": len(arms),
            "candidate_count": len(candidate_rows),
        },
        "attribution": attribution,
    }
    if cumulative_report is not None:
        extras["cumulative_summary"] = cumulative_report.get("cumulative_summary", {})
        extras["cumulative_continuation_policy"] = cumulative_report.get("continuation_policy", {})
    if trigger_policy_review is not None:
        extras["trigger_policy_review"] = {
            "decision": trigger_policy_review.get("decision"),
            "review_summary": trigger_policy_review.get("review_summary", {}),
            "continuation_policy": trigger_policy_review.get("continuation_policy", {}),
            "batch3_static_risk_preview": trigger_policy_review.get(
                "batch3_static_risk_preview", []
            ),
        }
    report = generate_report(
        phase="3.17.closed_loop",
        decision=decision,
        gate_results=gates,
        extras=extras,
    )
    return report, pairs, arms, attribution


def write_closed_loop_evidence(
    *,
    candidate_rows: list[dict[str, Any]],
    pair_summary: list[dict[str, Any]],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
    cumulative_report: dict[str, Any] | None = None,
    trigger_policy_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report, pairs, arms, attribution = closed_loop_report(
        candidate_rows=candidate_rows,
        pair_summary=pair_summary,
        cumulative_report=cumulative_report,
        trigger_policy_review=trigger_policy_review,
    )
    pairs_path = output_dir / "closed_loop_pairs.jsonl"
    arms_path = output_dir / "closed_loop_arms.jsonl"
    attribution_path = output_dir / "closed_loop_attribution_report.json"
    report_path = output_dir / "closed_loop_evidence_report.json"
    manifest_path = output_dir / "closed_loop_manifest.json"

    write_jsonl(pairs_path, pairs)
    write_jsonl(arms_path, arms)
    attribution_path.write_text(
        json.dumps(attribution, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    artifacts = [_artifact(path) for path in [pairs_path, arms_path, attribution_path, report_path]]
    for path in input_artifacts or []:
        if path.exists():
            artifacts.append(_artifact(path))
    manifest = generate_manifest(
        phase="3.17.closed_loop",
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = CLOSED_LOOP_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "pairs_path": pairs_path,
        "arms_path": arms_path,
        "attribution_path": attribution_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }
