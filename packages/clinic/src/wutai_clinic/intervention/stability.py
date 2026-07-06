from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.attribution import attribute_pair_summaries, classify_pair_summary
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

BATCH_STABILITY_VERSION = "phase318_small_batch_stability_v1"
CLAIM_BOUNDARY = (
    "Small-batch stability evidence summarizes completed official-eval pair summaries. "
    "It can guide the next batch, but it does not make a generalized uplift claim, does "
    "not validate EFE/STR prediction, and does not authorize unattended full-scale runs."
)
POSITIVE_LABEL = "intervention_only_resolved_trigger_hit_candidate"
NEGATIVE_LABEL = "control_only_resolved_trigger_hit_negative_candidate"
NEUTRAL_LABELS = {
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
}


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


def _main_rows(pair_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in pair_summary if classify_pair_summary(row) == "main_treatment"]


def _label_count(rows: list[dict[str, Any]], label: str) -> int:
    return sum(row.get("effect_label") == label for row in rows)


def stability_summary(
    pair_summary: list[dict[str, Any]],
    *,
    target_main_pairs: int = 4,
) -> dict[str, Any]:
    attribution = attribute_pair_summaries(pair_summary)
    main = _main_rows(pair_summary)
    positive_count = _label_count(main, POSITIVE_LABEL)
    negative_count = _label_count(main, NEGATIVE_LABEL)
    neutral_count = sum(row.get("effect_label") in NEUTRAL_LABELS for row in main)
    main_count = len(main)
    return {
        "total_pair_count": len(pair_summary),
        "main_treatment_pair_count": main_count,
        "trigger_miss_pair_count": attribution["trigger_miss_pairs"],
        "positive_main_count": positive_count,
        "neutral_main_count": neutral_count,
        "negative_main_count": negative_count,
        "main_positive_rate": positive_count / main_count if main_count else None,
        "main_negative_rate": negative_count / main_count if main_count else None,
        "trigger_hit_rate": attribution["trigger_hit_rate"],
        "control_success_rate": attribution["control_success_rate"],
        "intervention_success_rate": attribution["intervention_success_rate"],
        "resolved_delta": attribution["resolved_delta"],
        "classification_counts": attribution["classification_counts"],
        "main_pair_ids": attribution["outcome_summary"]["main_pair_ids"],
        "secondary_pair_ids": attribution["outcome_summary"]["secondary_pair_ids"],
        "target_main_pairs": target_main_pairs,
        "target_main_pairs_met": main_count >= target_main_pairs,
        "attribution": attribution,
    }


def stability_gates(
    pair_summary: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    min_total_pairs: int = 4,
    max_total_pairs: int = 8,
) -> dict[str, bool]:
    main = _main_rows(pair_summary)
    return {
        "pair_summary_rows_present": len(pair_summary) > 0,
        "official_eval_completed": all(
            row.get("official_eval_completed") is True for row in pair_summary
        ),
        "small_batch_total_window": min_total_pairs <= len(pair_summary) <= max_total_pairs,
        "attribution_classification_valid": summary["classification_counts"]["invalid"] == 0,
        "main_treatment_pairs_present": summary["main_treatment_pair_count"] > 0,
        "main_patch_application_complete": bool(main)
        and all(row.get("patches_applied") is True for row in main),
        "negative_main_pairs_not_observed": summary["negative_main_count"] == 0,
        "non_negative_resolved_delta": int(summary["resolved_delta"]) >= 0,
        "claim_boundary_present": bool(CLAIM_BOUNDARY),
        "generalized_uplift_claim_not_made": True,
        "efe_str_predictive_claim_not_made": True,
        "full_unattended_run_not_authorized": True,
    }


def continuation_policy(summary: dict[str, Any], gates: dict[str, bool]) -> dict[str, Any]:
    enough_main = bool(summary["target_main_pairs_met"])
    positive = int(summary["positive_main_count"])
    negative = int(summary["negative_main_count"])
    safe_to_continue = all(gates.values()) and positive >= negative
    return {
        "allow_next_small_batch": safe_to_continue,
        "allow_stability_claim": safe_to_continue and enough_main and positive > 0,
        "allow_full_64_unattended": False,
        "allow_intervention_effect_claim": False,
        "allow_efe_str_predictive_claim": False,
        "recommended_next_step": (
            "collect_more_main_attribution_pairs_after_trigger_policy_gate"
            if not enough_main
            else "expand_one_more_small_batch_after_trigger_policy_gate_before_any_claim"
        ),
        "evidence_note": (
            f"{summary['main_treatment_pair_count']} main pairs, "
            f"{positive} positive, {summary['neutral_main_count']} neutral, {negative} negative, "
            f"target_main_pairs={summary['target_main_pairs']}."
        ),
    }


def stability_decision(summary: dict[str, Any], gates: dict[str, bool]) -> str:
    if not all(gates.values()):
        return "batch_stability_evidence_blocked"
    if not summary["target_main_pairs_met"]:
        return "batch_stability_probe_needs_more_main_pairs"
    if int(summary["negative_main_count"]) > int(summary["positive_main_count"]):
        return "batch_stability_negative_risk_review_required"
    return "batch_stability_small_batch_ready_for_expansion"


def batch_stability_report(
    pair_summary: list[dict[str, Any]],
    *,
    target_main_pairs: int = 4,
    min_total_pairs: int = 4,
    max_total_pairs: int = 8,
) -> dict[str, Any]:
    summary = stability_summary(pair_summary, target_main_pairs=target_main_pairs)
    gates = stability_gates(
        pair_summary,
        summary,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
    )
    policy = continuation_policy(summary, gates)
    return generate_report(
        phase="3.18.batch_stability",
        decision=stability_decision(summary, gates),
        gate_results=gates,
        extras={
            "version": BATCH_STABILITY_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "stability_summary": summary,
            "continuation_policy": policy,
        },
    )


def write_batch_stability_evidence(
    *,
    pair_summary: list[dict[str, Any]],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
    target_main_pairs: int = 4,
    min_total_pairs: int = 4,
    max_total_pairs: int = 8,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = batch_stability_report(
        pair_summary,
        target_main_pairs=target_main_pairs,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
    )
    pairs_path = output_dir / "batch_stability_pairs.jsonl"
    summary_path = output_dir / "batch_stability_summary.json"
    report_path = output_dir / "batch_stability_report.json"
    manifest_path = output_dir / "batch_stability_manifest.json"

    annotated_rows = [
        {**row, "stability_classification": classify_pair_summary(row)} for row in pair_summary
    ]
    write_jsonl(pairs_path, annotated_rows)
    summary_path.write_text(
        json.dumps(report["stability_summary"], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = [_artifact(path) for path in [pairs_path, summary_path, report_path]]
    for path in input_artifacts or []:
        if path.exists():
            artifacts.append(_artifact(path))
    manifest = generate_manifest(
        phase="3.18.batch_stability",
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = BATCH_STABILITY_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "pairs_path": pairs_path,
        "summary_path": summary_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "BATCH_STABILITY_VERSION",
    "CLAIM_BOUNDARY",
    "batch_stability_report",
    "stability_summary",
    "write_batch_stability_evidence",
]
