from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V1_BATCH_OUTCOMES_PHASE = "6.protocol_v1_batch_outcomes"
PROTOCOL_V1_BATCH_OUTCOMES_VERSION = "phase6_protocol_v1_batch_outcomes_v1"
POSITIVE_LABEL = "intervention_only_resolved_trigger_hit_candidate"
NEGATIVE_LABEL = "control_only_resolved_trigger_hit_negative_candidate"
NO_UPLIFT_LABELS = {
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
}
CLAIM_BOUNDARY = (
    "Protocol v1 batch outcomes aggregate outcome-backed pairs by protocol family. "
    "Protocol v1 replay-prefix/hook evidence is not mixed with v0 state-capsule evidence, "
    "and this report does not make a generalized uplift or predictive EFE/STR claim."
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl" and path.is_file():
        with path.open("rb") as handle:
            record_count = sum(1 for line in handle if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": record_count,
        "exists": path.is_file(),
    }


def _first_jsonl_row(path: Path) -> dict[str, Any]:
    rows = list(read_jsonl(path)) if path.is_file() else []
    return rows[0] if rows else {}


def _resolve_live_pair_summary(scorecard_path: Path, official_report: dict[str, Any]) -> Path:
    pair_dir = official_report.get("pair_dir")
    if isinstance(pair_dir, str) and pair_dir:
        candidate = Path(pair_dir)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate / "protocol_v1_live_pair_summary.jsonl"
        if candidate.is_file():
            return candidate
    parts = scorecard_path.parts
    if "protocol_v1_fresh_official_eval" in parts:
        root = Path(*parts[: parts.index("protocol_v1_fresh_official_eval")])
        task_id = scorecard_path.parent.name
        candidate = (
            root
            / "protocol_v1_fresh_live_pair"
            / task_id
            / "protocol_v1_live_pair_summary.jsonl"
        )
        if candidate.is_file():
            return candidate
    return scorecard_path.parent / "__missing_protocol_v1_live_pair_summary.jsonl"


def _patch_diverged(row: dict[str, Any]) -> bool | None:
    control = row.get("control_patch_archive_sha256") or row.get("control_patch_sha256")
    intervention = row.get("intervention_patch_archive_sha256") or row.get(
        "intervention_patch_sha256"
    )
    if not control or not intervention:
        return None
    return str(control) != str(intervention)


def _trajectory_outcome_class(row: dict[str, Any]) -> str:
    if row.get("official_eval_completed") is not True:
        return "pending_official_eval"
    label = str(row.get("effect_label") or "")
    if label == POSITIVE_LABEL:
        return "outcome_uplift"
    if label == NEGATIVE_LABEL:
        return "outcome_regression"
    if label in NO_UPLIFT_LABELS:
        if _patch_diverged(row) is True:
            return "trajectory_diverged_no_uplift"
        return "hook_no_behavior_shift_no_uplift"
    return "unknown_outcome_label"


def _normalize_protocol_v1_pair(scorecard_path: Path) -> dict[str, Any]:
    scorecard = _load_json(scorecard_path)
    official_report_path = scorecard_path.with_name("protocol_v1_official_eval_report.json")
    official_report = _load_json(official_report_path) if official_report_path.is_file() else {}
    live_summary_path = _resolve_live_pair_summary(scorecard_path, official_report)
    live_summary = _first_jsonl_row(live_summary_path)
    merged = {**live_summary, **scorecard}
    row = {
        "protocol_family": "protocol_v1_constraint_hook",
        "pair_id": merged.get("pair_id"),
        "source_task_id": merged.get("source_task_id"),
        "effect_label": merged.get("effect_label"),
        "control_resolved": merged.get("control_resolved"),
        "treatment_resolved": merged.get("treatment_resolved")
        if "treatment_resolved" in merged
        else merged.get("intervention_resolved"),
        "official_eval_completed": merged.get("official_eval_completed") is True,
        "outcome_source": merged.get("outcome_source"),
        "replay_prefix_output_hashes_match": live_summary.get(
            "replay_prefix_output_hashes_match"
        ),
        "treatment_hook_event_count": live_summary.get("treatment_hook_event_count"),
        "control_patch_archive_sha256": live_summary.get("control_patch_archive_sha256"),
        "intervention_patch_archive_sha256": live_summary.get(
            "intervention_patch_archive_sha256"
        ),
        "state_capsule_equivalence_claimed": scorecard.get(
            "state_capsule_equivalence_claimed"
        ),
        "scorecard_path": scorecard_path.as_posix(),
        "official_report_path": official_report_path.as_posix()
        if official_report_path.is_file()
        else None,
        "live_pair_summary_path": live_summary_path.as_posix()
        if live_summary_path.is_file()
        else None,
    }
    row["patch_diverged"] = _patch_diverged(row)
    row["trajectory_outcome_class"] = _trajectory_outcome_class(row)
    return row


def _normalize_v0_reference_pair(scorecard_path: Path) -> dict[str, Any]:
    scorecard = _load_json(scorecard_path)
    row = {
        "protocol_family": "protocol_v0_state_capsule_reference",
        "pair_id": scorecard.get("pair_id"),
        "source_task_id": scorecard.get("source_task_id"),
        "effect_label": scorecard.get("effect_label"),
        "control_resolved": scorecard.get("control_resolved"),
        "treatment_resolved": scorecard.get("treatment_resolved")
        if "treatment_resolved" in scorecard
        else scorecard.get("intervention_resolved"),
        "official_eval_completed": scorecard.get("official_eval_completed") is True,
        "outcome_source": scorecard.get("outcome_source"),
        "intervention_injected_once": scorecard.get("intervention_injected_once"),
        "state_capsule_equivalent": scorecard.get("state_capsule_equivalent"),
        "scorecard_path": scorecard_path.as_posix(),
    }
    row["trajectory_outcome_class"] = _trajectory_outcome_class(row)
    return row


def load_protocol_v1_outcome_rows(root: Path) -> list[dict[str, Any]]:
    return [
        _normalize_protocol_v1_pair(path)
        for path in sorted(root.rglob("protocol_v1_dual_scorecard.json"))
    ]


def load_v0_reference_outcome_rows(root: Path) -> list[dict[str, Any]]:
    return [
        _normalize_v0_reference_pair(path)
        for path in sorted(root.glob("*/phase6_dual_scorecard.json"))
    ]


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("effect_label") or "missing") for row in rows))


def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("trajectory_outcome_class") or "missing") for row in rows))


def protocol_v1_batch_outcomes_summary(
    *,
    protocol_v1_rows: list[dict[str, Any]],
    v0_reference_rows: list[dict[str, Any]] | None = None,
    target_protocol_v1_pair_count: int = 4,
) -> dict[str, Any]:
    references = list(v0_reference_rows or [])
    positive = sum(row.get("effect_label") == POSITIVE_LABEL for row in protocol_v1_rows)
    negative = sum(row.get("effect_label") == NEGATIVE_LABEL for row in protocol_v1_rows)
    no_uplift = sum(row.get("effect_label") in NO_UPLIFT_LABELS for row in protocol_v1_rows)
    completed = sum(row.get("official_eval_completed") is True for row in protocol_v1_rows)
    return {
        "protocol_v1_pair_count": len(protocol_v1_rows),
        "protocol_v1_official_completed_count": completed,
        "protocol_v1_positive_count": positive,
        "protocol_v1_no_uplift_count": no_uplift,
        "protocol_v1_negative_count": negative,
        "protocol_v1_label_counts": _label_counts(protocol_v1_rows),
        "protocol_v1_trajectory_outcome_counts": _class_counts(protocol_v1_rows),
        "protocol_v1_source_task_ids": [
            row.get("source_task_id") for row in protocol_v1_rows if row.get("source_task_id")
        ],
        "target_protocol_v1_pair_count": target_protocol_v1_pair_count,
        "target_protocol_v1_pair_count_met": (
            len(protocol_v1_rows) >= target_protocol_v1_pair_count
        ),
        "v0_reference_pair_count": len(references),
        "v0_reference_label_counts": _label_counts(references),
        "v0_reference_trajectory_outcome_counts": _class_counts(references),
    }


def protocol_v1_batch_outcomes_gates(
    protocol_v1_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, bool]:
    return {
        "protocol_v1_rows_present": len(protocol_v1_rows) > 0,
        "protocol_v1_official_eval_completed": bool(protocol_v1_rows)
        and all(row.get("official_eval_completed") is True for row in protocol_v1_rows),
        "protocol_v1_outcome_source_official_eval": bool(protocol_v1_rows)
        and all(row.get("outcome_source") == "official_eval" for row in protocol_v1_rows),
        "protocol_v1_replay_prefix_equivalent": bool(protocol_v1_rows)
        and all(row.get("replay_prefix_output_hashes_match") is True for row in protocol_v1_rows),
        "protocol_v1_hook_observed": bool(protocol_v1_rows)
        and all(int(row.get("treatment_hook_event_count") or 0) > 0 for row in protocol_v1_rows),
        "protocol_v1_state_capsule_equivalence_not_claimed": bool(protocol_v1_rows)
        and all(row.get("state_capsule_equivalence_claimed") is False for row in protocol_v1_rows),
        "stratifies_v0_reference_separately": True,
        "claim_boundary_present": bool(CLAIM_BOUNDARY),
        "generalized_uplift_claim_not_made": True,
        "efe_str_predictive_claim_not_made": True,
        "full_unattended_run_not_authorized": True,
        "target_count_recorded": int(summary["target_protocol_v1_pair_count"]) > 0,
    }


def protocol_v1_batch_outcomes_decision(
    summary: dict[str, Any],
    gates: dict[str, bool],
) -> str:
    if not all(gates.values()):
        return "protocol_v1_batch_outcomes_blocked"
    if not summary["target_protocol_v1_pair_count_met"]:
        if int(summary["protocol_v1_no_uplift_count"]) == summary["protocol_v1_pair_count"]:
            return "protocol_v1_batch_outcomes_underpowered_no_uplift_observed"
        return "protocol_v1_batch_outcomes_underpowered_continue_sampling"
    if int(summary["protocol_v1_negative_count"]) > int(summary["protocol_v1_positive_count"]):
        return "protocol_v1_batch_outcomes_negative_risk_review_required"
    if int(summary["protocol_v1_positive_count"]) == 0:
        return "protocol_v1_batch_outcomes_no_uplift_needs_prescription_revision"
    return "protocol_v1_batch_outcomes_ready_for_next_small_batch"


def protocol_v1_batch_outcomes_continuation_policy(
    summary: dict[str, Any],
    gates: dict[str, bool],
) -> dict[str, Any]:
    structural_passed = all(gates.values())
    target_met = bool(summary["target_protocol_v1_pair_count_met"])
    positive = int(summary["protocol_v1_positive_count"])
    negative = int(summary["protocol_v1_negative_count"])
    no_uplift = int(summary["protocol_v1_no_uplift_count"])
    return {
        "allow_more_protocol_v1_pairs": structural_passed and not target_met and negative == 0,
        "allow_protocol_v2_prescription_design": structural_passed
        and no_uplift > 0
        and positive == 0,
        "allow_protocol_v1_stability_claim": structural_passed and target_met and positive > 0,
        "allow_intervention_effect_claim": False,
        "allow_efe_str_predictive_claim": False,
        "allow_full_64_unattended": False,
        "recommended_next_step": (
            "run_remaining_protocol_v1_fresh_pairs_before_any_stability_claim"
            if structural_passed and not target_met
            else "design_protocol_v2_prescription_for_no_uplift_dynamics"
            if structural_passed and positive == 0
            else "review_blocking_evidence_before_running_more_pairs"
            if not structural_passed
            else "expand_one_more_small_batch_after_trigger_policy_gate"
        ),
        "evidence_note": (
            f"{summary['protocol_v1_pair_count']} Protocol v1 pairs, "
            f"{positive} positive, {no_uplift} no-uplift, {negative} negative; "
            f"target={summary['target_protocol_v1_pair_count']}."
        ),
    }


def protocol_v1_batch_outcomes_report(
    *,
    protocol_v1_rows: list[dict[str, Any]],
    v0_reference_rows: list[dict[str, Any]] | None = None,
    target_protocol_v1_pair_count: int = 4,
) -> dict[str, Any]:
    summary = protocol_v1_batch_outcomes_summary(
        protocol_v1_rows=protocol_v1_rows,
        v0_reference_rows=v0_reference_rows,
        target_protocol_v1_pair_count=target_protocol_v1_pair_count,
    )
    gates = protocol_v1_batch_outcomes_gates(protocol_v1_rows, summary)
    return generate_report(
        phase=PROTOCOL_V1_BATCH_OUTCOMES_PHASE,
        decision=protocol_v1_batch_outcomes_decision(summary, gates),
        gate_results=gates,
        extras={
            "version": PROTOCOL_V1_BATCH_OUTCOMES_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "summary": summary,
            "continuation_policy": protocol_v1_batch_outcomes_continuation_policy(
                summary,
                gates,
            ),
        },
    )


def write_protocol_v1_batch_outcomes_evidence(
    *,
    root: Path,
    output_dir: Path,
    include_v0_reference: bool = True,
    target_protocol_v1_pair_count: int = 4,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol_v1_rows = load_protocol_v1_outcome_rows(root)
    v0_reference_rows = load_v0_reference_outcome_rows(root) if include_v0_reference else []
    report = protocol_v1_batch_outcomes_report(
        protocol_v1_rows=protocol_v1_rows,
        v0_reference_rows=v0_reference_rows,
        target_protocol_v1_pair_count=target_protocol_v1_pair_count,
    )

    pairs_path = output_dir / "protocol_v1_batch_outcomes_pairs.jsonl"
    reference_path = output_dir / "protocol_v1_batch_outcomes_v0_reference_pairs.jsonl"
    summary_path = output_dir / "protocol_v1_batch_outcomes_summary.json"
    report_path = output_dir / "protocol_v1_batch_outcomes_report.json"
    manifest_path = output_dir / "protocol_v1_batch_outcomes_manifest.json"

    write_jsonl(pairs_path, protocol_v1_rows)
    write_jsonl(reference_path, v0_reference_rows)
    _write_json(summary_path, report["summary"])
    _write_json(report_path, report)
    artifacts = [
        _artifact(path) for path in [pairs_path, reference_path, summary_path, report_path]
    ]
    artifacts.extend(_artifact(Path(row["scorecard_path"])) for row in protocol_v1_rows)
    artifacts.extend(_artifact(Path(row["scorecard_path"])) for row in v0_reference_rows)
    manifest = generate_manifest(
        phase=PROTOCOL_V1_BATCH_OUTCOMES_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V1_BATCH_OUTCOMES_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "pairs_path": pairs_path,
        "reference_path": reference_path,
        "summary_path": summary_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "CLAIM_BOUNDARY",
    "PROTOCOL_V1_BATCH_OUTCOMES_VERSION",
    "load_protocol_v1_outcome_rows",
    "load_v0_reference_outcome_rows",
    "protocol_v1_batch_outcomes_report",
    "write_protocol_v1_batch_outcomes_evidence",
]
