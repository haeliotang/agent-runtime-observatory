from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

BATCH3_READINESS_VERSION = "phase319_batch3_readiness_v1"
EXPECTED_RECALIBRATION_DECISION = (
    "phase316_live_trigger_recalibration_protocol_ready_not_executable_batch3_not_authorized"
)
EXPECTED_DRY_RUN_DECISION = (
    "phase316_live_feature_hook_dry_run_gate_passed_batch3_still_not_authorized"
)
CLAIM_BOUNDARY = (
    "Batch-3 readiness gates integrate completed small-batch stability, trigger-policy "
    "review, recalibration protocol, and optional live-feature hook dry-run evidence. "
    "They authorize preparation and review only; they do not start Docker, call a model, "
    "claim generalized uplift, or authorize external-provider execution."
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


def _all_candidates(candidate_rows: list[dict[str, Any]], key: str, expected: Any) -> bool:
    return bool(candidate_rows) and all(row.get(key) == expected for row in candidate_rows)


def readiness_summary(
    *,
    stability_report: dict[str, Any],
    trigger_policy_review: dict[str, Any],
    recalibration_report: dict[str, Any],
    recalibration_protocol: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stability = stability_report.get("stability_summary", {})
    trigger_policy = trigger_policy_review.get("continuation_policy", {})
    recalibration_summary = recalibration_report.get("summary", {})
    dry_run_summary = (
        live_feature_dry_run_report.get("summary", {}) if live_feature_dry_run_report else {}
    )
    return {
        "stability_decision": stability_report.get("decision"),
        "stability_main_treatment_pair_count": stability.get("main_treatment_pair_count"),
        "stability_positive_main_count": stability.get("positive_main_count"),
        "stability_negative_main_count": stability.get("negative_main_count"),
        "stability_trigger_hit_rate": stability.get("trigger_hit_rate"),
        "stability_allow_next_small_batch": stability_report.get(
            "continuation_policy", {}
        ).get("allow_next_small_batch"),
        "trigger_review_decision": trigger_policy_review.get("decision"),
        "same_static_prefix_allowed": trigger_policy.get(
            "allow_batch3_same_static_prefix_policy"
        ),
        "recalibration_required": trigger_policy.get(
            "require_live_trigger_recalibration_protocol_before_batch3"
        ),
        "recalibration_decision": recalibration_report.get("decision"),
        "recalibration_candidate_count": recalibration_summary.get("candidate_count"),
        "protocol_version": recalibration_protocol.get("protocol_version"),
        "protocol_candidate_count": recalibration_protocol.get("batch3_candidate_count"),
        "candidate_count": len(candidate_rows),
        "candidate_policy_counts": _policy_counts(candidate_rows),
        "dry_run_present": live_feature_dry_run_report is not None,
        "dry_run_decision": (
            live_feature_dry_run_report.get("decision") if live_feature_dry_run_report else None
        ),
        "dry_run_injection_count": dry_run_summary.get("injection_count"),
        "dry_run_candidate_count": dry_run_summary.get("candidate_count"),
        "real_run_authorized": False,
    }


def _policy_counts(candidate_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in candidate_rows:
        policy_id = str(row.get("intervention_policy_id", "unknown"))
        counts[policy_id] = counts.get(policy_id, 0) + 1
    return dict(sorted(counts.items()))


def readiness_gates(
    *,
    stability_report: dict[str, Any],
    trigger_policy_review: dict[str, Any],
    recalibration_report: dict[str, Any],
    recalibration_protocol: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> dict[str, bool]:
    stability_policy = stability_report.get("continuation_policy", {})
    trigger_policy = trigger_policy_review.get("continuation_policy", {})
    runner_delta = recalibration_protocol.get("runner_contract_delta", {})
    decision_boundary = recalibration_protocol.get("decision_boundary", {})
    expected_candidate_count = recalibration_protocol.get("batch3_candidate_count")
    recalibration_summary = recalibration_report.get("summary", {})
    gates = {
        "stability_report_passed": stability_report.get("passed") is True,
        "stability_allows_next_small_batch": (
            stability_policy.get("allow_next_small_batch") is True
        ),
        "stability_blocks_effect_claim": (
            stability_policy.get("allow_intervention_effect_claim") is False
        ),
        "stability_blocks_efe_str_claim": (
            stability_policy.get("allow_efe_str_predictive_claim") is False
        ),
        "stability_blocks_full_unattended": (
            stability_policy.get("allow_full_64_unattended") is False
        ),
        "trigger_review_passed": trigger_policy_review.get("passed") is True,
        "trigger_review_requires_recalibration": (
            trigger_policy.get("require_live_trigger_recalibration_protocol_before_batch3")
            is True
        ),
        "same_static_prefix_policy_blocked": (
            trigger_policy.get("allow_batch3_same_static_prefix_policy") is False
        ),
        "trigger_review_blocks_real_run_without_authorization": (
            trigger_policy.get("allow_batch3_real_run_without_new_authorization") is False
        ),
        "recalibration_report_passed": recalibration_report.get("passed") is True,
        "recalibration_decision_expected": (
            recalibration_report.get("decision") == EXPECTED_RECALIBRATION_DECISION
        ),
        "recalibration_protocol_version_present": bool(
            recalibration_protocol.get("protocol_version")
        ),
        "recalibration_protocol_requires_dry_run": (
            runner_delta.get("dry_run_required_before_real_run") is True
        ),
        "recalibration_protocol_requires_external_authorization": (
            runner_delta.get("new_external_provider_authorization_required_before_real_run")
            is True
        ),
        "recalibration_protocol_does_not_change_runner": (
            runner_delta.get("runner_code_changed_by_this_protocol") is False
        ),
        "recalibration_protocol_blocks_real_run": (
            decision_boundary.get("batch3_authorized") is False
        ),
        "candidate_rows_present": len(candidate_rows) > 0,
        "candidate_count_matches_protocol": len(candidate_rows) == expected_candidate_count,
        "candidate_count_matches_recalibration_report": len(candidate_rows)
        == recalibration_summary.get("candidate_count"),
        "candidate_rows_not_authorized": _all_candidates(
            candidate_rows, "batch3_real_run_authorized", False
        ),
        "candidate_rows_disable_exact_static_prefix": _all_candidates(
            candidate_rows, "exact_static_prefix_trigger_disabled", True
        ),
        "candidate_rows_use_live_feature_mode": _all_candidates(
            candidate_rows, "recalibrated_trigger_mode", "live_feature_signature_window"
        ),
        "claim_boundary_present": bool(CLAIM_BOUNDARY),
    }
    if live_feature_dry_run_report is not None:
        dry_summary = live_feature_dry_run_report.get("summary", {})
        dry_policy = live_feature_dry_run_report.get("continuation_policy", {})
        gates.update(
            {
                "dry_run_report_passed": live_feature_dry_run_report.get("passed") is True,
                "dry_run_decision_expected": (
                    live_feature_dry_run_report.get("decision") == EXPECTED_DRY_RUN_DECISION
                ),
                "dry_run_candidate_count_matches": dry_summary.get("candidate_count")
                == len(candidate_rows),
                "dry_run_injected_once_per_candidate": dry_summary.get("injection_count")
                == len(candidate_rows),
                "dry_run_blocks_real_run": dry_policy.get("allow_batch3_real_run") is False,
                "dry_run_started_no_model_or_runner": (
                    dry_summary.get("model_call_started") is False
                    and dry_summary.get("runner_started") is False
                    and dry_summary.get("docker_or_official_eval_started") is False
                ),
            }
        )
    return gates


def readiness_decision(
    gates: dict[str, bool],
    *,
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> str:
    if not all(gates.values()):
        return "batch3_readiness_blocked"
    if live_feature_dry_run_report is None:
        return "batch3_readiness_recalibration_ready_live_feature_dry_run_required"
    return "batch3_readiness_live_feature_dry_run_ready_external_run_not_authorized"


def continuation_policy(
    gates: dict[str, bool],
    *,
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    passed = all(gates.values())
    dry_run_ready = passed and live_feature_dry_run_report is not None
    return {
        "allow_prepare_batch3_candidate_review": passed,
        "allow_live_hook_runner_preflight": dry_run_ready,
        "allow_batch3_static_prefix_run": False,
        "allow_batch3_real_run": False,
        "allow_full_64_unattended": False,
        "allow_intervention_effect_claim": False,
        "allow_efe_str_predictive_claim": False,
        "recommended_next_step": (
            "prepare_operator_approved_live_hook_runner_preflight_and_external_provider_export"
            if dry_run_ready
            else "run_live_feature_hook_dry_run_gate_before_any_external_provider_batch3"
        ),
    }


def batch3_readiness_report(
    *,
    stability_report: dict[str, Any],
    trigger_policy_review: dict[str, Any],
    recalibration_report: dict[str, Any],
    recalibration_protocol: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates = readiness_gates(
        stability_report=stability_report,
        trigger_policy_review=trigger_policy_review,
        recalibration_report=recalibration_report,
        recalibration_protocol=recalibration_protocol,
        candidate_rows=candidate_rows,
        live_feature_dry_run_report=live_feature_dry_run_report,
    )
    return generate_report(
        phase="3.19.batch3_readiness",
        decision=readiness_decision(
            gates,
            live_feature_dry_run_report=live_feature_dry_run_report,
        ),
        gate_results=gates,
        extras={
            "version": BATCH3_READINESS_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "readiness_summary": readiness_summary(
                stability_report=stability_report,
                trigger_policy_review=trigger_policy_review,
                recalibration_report=recalibration_report,
                recalibration_protocol=recalibration_protocol,
                candidate_rows=candidate_rows,
                live_feature_dry_run_report=live_feature_dry_run_report,
            ),
            "continuation_policy": continuation_policy(
                gates,
                live_feature_dry_run_report=live_feature_dry_run_report,
            ),
        },
    )


def write_batch3_readiness_evidence(
    *,
    stability_report: dict[str, Any],
    trigger_policy_review: dict[str, Any],
    recalibration_report: dict[str, Any],
    recalibration_protocol: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
    live_feature_dry_run_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = batch3_readiness_report(
        stability_report=stability_report,
        trigger_policy_review=trigger_policy_review,
        recalibration_report=recalibration_report,
        recalibration_protocol=recalibration_protocol,
        candidate_rows=candidate_rows,
        live_feature_dry_run_report=live_feature_dry_run_report,
    )
    candidates_path = output_dir / "batch3_readiness_candidates.jsonl"
    summary_path = output_dir / "batch3_readiness_summary.json"
    report_path = output_dir / "batch3_readiness_report.json"
    manifest_path = output_dir / "batch3_readiness_manifest.json"

    write_jsonl(candidates_path, candidate_rows)
    summary_path.write_text(
        json.dumps(report["readiness_summary"], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = [_artifact(path) for path in [candidates_path, summary_path, report_path]]
    for path in input_artifacts or []:
        if path.exists():
            artifacts.append(_artifact(path))
    manifest = generate_manifest(
        phase="3.19.batch3_readiness",
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = BATCH3_READINESS_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "candidates_path": candidates_path,
        "summary_path": summary_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "BATCH3_READINESS_VERSION",
    "CLAIM_BOUNDARY",
    "batch3_readiness_report",
    "readiness_summary",
    "write_batch3_readiness_evidence",
]
