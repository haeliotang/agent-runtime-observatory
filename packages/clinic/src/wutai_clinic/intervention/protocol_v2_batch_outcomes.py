from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V2_BATCH_OUTCOMES_PHASE = "6.protocol_v2_batch_outcomes"
PROTOCOL_V2_BATCH_OUTCOMES_VERSION = "phase6_protocol_v2_batch_outcomes_v1"

# v2 effect labels
V2_POSITIVE_LABEL = "intervention_only_resolved_trigger_hit_candidate"
V2_NEGATIVE_LABEL = "control_only_resolved_trigger_hit_negative_candidate"
V2_NO_UPLIFT_LABELS = {
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
}

# v1 labels (for separately stratified v1 reference rows)
V1_POSITIVE_LABEL = "intervention_only_resolved_trigger_hit_candidate"
V1_NO_UPLIFT_LABELS = {
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
}

CLAIM_BOUNDARY = (
    "Protocol v2 batch outcomes aggregate outcome-backed pairs by protocol family. "
    "Protocol v2 live-feature/hook evidence is not mixed with v1 replay-prefix or v0 "
    "state-capsule evidence, and this report does not make a generalized uplift or "
    "predictive EFE/STR claim."
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


def _load_fresh_candidate_task_ids(fresh_list_path: Path) -> set[str]:
    """Return set of source_task_ids from the fresh candidate gate list."""
    if not fresh_list_path.is_file():
        return set()
    task_ids: set[str] = set()
    for row in read_jsonl(fresh_list_path):
        tid = row.get("source_task_id")
        if tid:
            task_ids.add(str(tid))
    return task_ids


def _v2_trajectory_outcome_class(row: dict[str, Any]) -> str:
    if row.get("official_eval_completed") is not True:
        return "pending_official_eval"
    label = str(row.get("effect_label") or "")
    if label == V2_POSITIVE_LABEL:
        return "outcome_uplift"
    if label == V2_NEGATIVE_LABEL:
        return "outcome_regression"
    if label in V2_NO_UPLIFT_LABELS:
        return "hook_no_behavior_shift_no_uplift"
    return "unknown_outcome_label"


def _normalize_protocol_v2_pair(
    scorecard_path: Path,
    *,
    fresh_task_ids: set[str],
) -> dict[str, Any]:
    """Load a v2 dual scorecard and annotate it with stratum lineage."""
    scorecard = _load_json(scorecard_path)
    official_report_path = scorecard_path.with_name("protocol_v2_official_eval_report.json")
    official_report = _load_json(official_report_path) if official_report_path.is_file() else {}

    source_task_id = str(scorecard.get("source_task_id") or "")
    in_fresh_list = source_task_id in fresh_task_ids

    # lineage: strict_fresh if in gate list, else reference
    lineage = "v2_strict_fresh" if in_fresh_list else "v2_reference"

    row: dict[str, Any] = {
        "protocol_family": "protocol_v2_constraint_hook",
        "lineage": lineage,
        "pair_id": scorecard.get("pair_id"),
        "source_task_id": source_task_id or None,
        "effect_label": scorecard.get("effect_label"),
        "control_resolved": scorecard.get("control_resolved"),
        "treatment_resolved": scorecard.get("treatment_resolved"),
        "official_eval_completed": scorecard.get("official_eval_completed") is True,
        "outcome_source": scorecard.get("outcome_source"),
        "single_pair_only": scorecard.get("single_pair_only"),
        "state_capsule_equivalence_claimed": scorecard.get("state_capsule_equivalence_claimed"),
        "behavior_control_type": scorecard.get("behavior_control_type"),
        "scorecard_path": scorecard_path.as_posix(),
        "official_report_path": official_report_path.as_posix()
        if official_report_path.is_file()
        else None,
        # carry through arm_reports summary from official eval report
        "official_eval_decision": official_report.get("decision"),
    }
    row["trajectory_outcome_class"] = _v2_trajectory_outcome_class(row)
    return row


def _normalize_v1_reference_pair(scorecard_path: Path) -> dict[str, Any]:
    """Load a Protocol v1 dual scorecard as a separately stratified reference row."""
    scorecard = _load_json(scorecard_path)
    row: dict[str, Any] = {
        "protocol_family": "protocol_v1_constraint_hook",
        "lineage": "v1_reference",
        "pair_id": scorecard.get("pair_id"),
        "source_task_id": scorecard.get("source_task_id"),
        "effect_label": scorecard.get("effect_label"),
        "control_resolved": scorecard.get("control_resolved"),
        "treatment_resolved": scorecard.get("treatment_resolved")
        if "treatment_resolved" in scorecard
        else scorecard.get("intervention_resolved"),
        "official_eval_completed": scorecard.get("official_eval_completed") is True,
        "outcome_source": scorecard.get("outcome_source"),
        "state_capsule_equivalence_claimed": scorecard.get("state_capsule_equivalence_claimed"),
        "scorecard_path": scorecard_path.as_posix(),
    }
    row["trajectory_outcome_class"] = _v2_trajectory_outcome_class(row)
    return row


def _normalize_v0_reference_pair(scorecard_path: Path) -> dict[str, Any]:
    """Load a v0 state-capsule dual scorecard as a separately stratified reference row."""
    scorecard = _load_json(scorecard_path)
    row: dict[str, Any] = {
        "protocol_family": "protocol_v0_state_capsule_reference",
        "lineage": "v0_reference",
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
    row["trajectory_outcome_class"] = _v2_trajectory_outcome_class(row)
    return row


def _find_fresh_list_path(root: Path) -> Path:
    """Locate the v2 fresh candidate gate list under root."""
    candidates = sorted(
        root.rglob("protocol_v2_fresh_candidate_set_candidates.jsonl")
    )
    if candidates:
        return candidates[0]
    return root / "protocol_v2_fresh_candidate_gate" / "protocol_v2_fresh_candidate_set_candidates.jsonl"


def load_protocol_v2_outcome_rows(
    root: Path,
    *,
    fresh_task_ids: set[str],
) -> list[dict[str, Any]]:
    """Load all protocol_v2_dual_scorecard.json files under root, annotating lineage."""
    return [
        _normalize_protocol_v2_pair(path, fresh_task_ids=fresh_task_ids)
        for path in sorted(root.rglob("protocol_v2_dual_scorecard.json"))
    ]


def load_v1_reference_outcome_rows(root: Path) -> list[dict[str, Any]]:
    """Load Protocol v1 dual scorecards as stratified reference context."""
    return [
        _normalize_v1_reference_pair(path)
        for path in sorted(root.rglob("protocol_v1_dual_scorecard.json"))
    ]


def load_v0_reference_outcome_rows(root: Path) -> list[dict[str, Any]]:
    """Load v0 state-capsule dual scorecards as stratified reference context."""
    return [
        _normalize_v0_reference_pair(path)
        for path in sorted(root.glob("*/phase6_dual_scorecard.json"))
    ]


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("effect_label") or "missing") for row in rows))


def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("trajectory_outcome_class") or "missing") for row in rows))


def protocol_v2_batch_outcomes_summary(
    *,
    strict_fresh_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    v1_reference_rows: list[dict[str, Any]] | None = None,
    v0_reference_rows: list[dict[str, Any]] | None = None,
    target_pair_count: int = 4,
    fresh_list_degraded: bool = False,
) -> dict[str, Any]:
    """Build summary counts across all strata — never mix layer counts."""
    v1_refs = list(v1_reference_rows or [])
    v0_refs = list(v0_reference_rows or [])

    # v2 strict-fresh stratum
    fresh_uplift = sum(r.get("effect_label") == V2_POSITIVE_LABEL for r in strict_fresh_rows)
    fresh_no_uplift = sum(r.get("effect_label") in V2_NO_UPLIFT_LABELS for r in strict_fresh_rows)
    fresh_harm = sum(r.get("effect_label") == V2_NEGATIVE_LABEL for r in strict_fresh_rows)
    fresh_completed = sum(r.get("official_eval_completed") is True for r in strict_fresh_rows)

    # v2 reference stratum (completed v2 pipeline but NOT in fresh gate list)
    ref_uplift = sum(r.get("effect_label") == V2_POSITIVE_LABEL for r in reference_rows)
    ref_no_uplift = sum(r.get("effect_label") in V2_NO_UPLIFT_LABELS for r in reference_rows)
    ref_harm = sum(r.get("effect_label") == V2_NEGATIVE_LABEL for r in reference_rows)

    # combined v2 totals (fresh + reference) — for convenience only
    all_v2 = strict_fresh_rows + reference_rows
    uplift_pair_count = fresh_uplift + ref_uplift
    harm_pair_count = fresh_harm + ref_harm

    return {
        # --- v2 strict-fresh stratum ---
        "strict_fresh_pair_count": len(strict_fresh_rows),
        "strict_fresh_official_completed_count": fresh_completed,
        "strict_fresh_uplift_count": fresh_uplift,
        "strict_fresh_no_uplift_count": fresh_no_uplift,
        "strict_fresh_harm_count": fresh_harm,
        "strict_fresh_label_counts": _label_counts(strict_fresh_rows),
        "strict_fresh_trajectory_outcome_counts": _class_counts(strict_fresh_rows),
        "strict_fresh_source_task_ids": [
            r.get("source_task_id") for r in strict_fresh_rows if r.get("source_task_id")
        ],
        # --- v2 reference stratum ---
        "reference_pair_count": len(reference_rows),
        "reference_uplift_count": ref_uplift,
        "reference_no_uplift_count": ref_no_uplift,
        "reference_harm_count": ref_harm,
        "reference_label_counts": _label_counts(reference_rows),
        "reference_trajectory_outcome_counts": _class_counts(reference_rows),
        "reference_source_task_ids": [
            r.get("source_task_id") for r in reference_rows if r.get("source_task_id")
        ],
        # --- v1 reference stratum (separate context) ---
        "v1_reference_pair_count": len(v1_refs),
        "v1_reference_label_counts": _label_counts(v1_refs),
        "v1_reference_trajectory_outcome_counts": _class_counts(v1_refs),
        # --- v0 reference stratum (separate context) ---
        "v0_reference_pair_count": len(v0_refs),
        "v0_reference_label_counts": _label_counts(v0_refs),
        "v0_reference_trajectory_outcome_counts": _class_counts(v0_refs),
        # --- cross-stratum aggregates (v2 only) ---
        "uplift_pair_count": uplift_pair_count,
        "harm_pair_count": harm_pair_count,
        "total_v2_pair_count": len(all_v2),
        "total_v2_label_counts": _label_counts(all_v2),
        # --- target tracking ---
        "target_pair_count": target_pair_count,
        "target_pair_count_met": len(strict_fresh_rows) >= target_pair_count,
        # --- fresh list health ---
        "fresh_list_degraded": fresh_list_degraded,
    }


def protocol_v2_batch_outcomes_gates(
    strict_fresh_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, bool]:
    # integrity gates apply to every aggregated v2 row regardless of stratum;
    # in the degraded fresh-list path all rows land in reference and must
    # still pass integrity checks without blocking on an empty fresh stratum
    all_v2_rows = strict_fresh_rows + reference_rows
    return {
        "protocol_v2_rows_present": len(all_v2_rows) > 0,
        "protocol_v2_official_eval_completed": bool(all_v2_rows)
        and all(r.get("official_eval_completed") is True for r in all_v2_rows),
        "protocol_v2_outcome_source_official_eval": bool(all_v2_rows)
        and all(r.get("outcome_source") == "official_eval" for r in all_v2_rows),
        "protocol_v2_state_capsule_equivalence_not_claimed": bool(all_v2_rows)
        and all(r.get("state_capsule_equivalence_claimed") is False for r in all_v2_rows),
        "stratifies_reference_separately": True,
        "claim_boundary_present": bool(CLAIM_BOUNDARY),
        "generalized_uplift_claim_not_made": True,
        "efe_str_predictive_claim_not_made": True,
        "full_unattended_run_not_authorized": True,
        "target_count_recorded": int(summary["target_pair_count"]) > 0,
        # degraded gate: if fresh list was missing, flag it but do not block
        "fresh_list_present": not summary["fresh_list_degraded"],
    }


def protocol_v2_batch_outcomes_decision(
    summary: dict[str, Any],
    gates: dict[str, bool],
) -> str:
    # gates that must always pass (fresh_list_present is advisory, not blocking)
    hard_gates = {k: v for k, v in gates.items() if k != "fresh_list_present"}
    if not all(hard_gates.values()):
        return "protocol_v2_batch_outcomes_blocked"
    if not summary["target_pair_count_met"]:
        # all completed and zero uplift observed
        if int(summary["strict_fresh_no_uplift_count"]) == summary["strict_fresh_pair_count"]:
            return "protocol_v2_batch_outcomes_underpowered_no_uplift_observed"
        return "protocol_v2_batch_outcomes_underpowered_continue_sampling"
    if int(summary["harm_pair_count"]) > int(summary["uplift_pair_count"]):
        return "protocol_v2_batch_outcomes_negative_risk_review_required"
    if int(summary["uplift_pair_count"]) == 0:
        return "protocol_v2_batch_outcomes_no_uplift_needs_prescription_revision"
    return "protocol_v2_batch_outcomes_ready_for_next_small_batch"


def protocol_v2_batch_outcomes_continuation_policy(
    summary: dict[str, Any],
    gates: dict[str, bool],
) -> dict[str, Any]:
    hard_gates = {k: v for k, v in gates.items() if k != "fresh_list_present"}
    structural_passed = all(hard_gates.values())
    target_met = bool(summary["target_pair_count_met"])
    uplift = int(summary["uplift_pair_count"])
    harm = int(summary["harm_pair_count"])
    no_uplift = int(summary["strict_fresh_no_uplift_count"])
    fresh_count = int(summary["strict_fresh_pair_count"])
    return {
        # ALLOWED
        "allow_continue_remaining_fresh_targets": structural_passed and not target_met and harm == 0,
        "allow_power_analysis_consuming_this_report": structural_passed and fresh_count > 0,
        # BLOCKED
        "allow_stability_claim": False,
        "allow_same_pair_positive_attribution": False,
        "allow_generalized_uplift_claim": False,
        "allow_efe_str_predictive_claim": False,
        "allow_full_unattended_run": False,
        "recommended_next_step": (
            "run_remaining_protocol_v2_fresh_targets_before_any_stability_claim"
            if structural_passed and not target_met
            else "revise_protocol_v2_prescription_for_no_uplift_dynamics"
            if structural_passed and uplift == 0
            else "review_blocking_evidence_before_running_more_pairs"
            if not structural_passed
            else "expand_one_more_small_batch_after_trigger_policy_gate"
        ),
        "evidence_note": (
            f"{fresh_count} Protocol v2 strict-fresh pairs, "
            f"{uplift} uplift, {no_uplift} no-uplift, {harm} harm; "
            f"target={summary['target_pair_count']}."
        ),
    }


def protocol_v2_batch_outcomes_report(
    *,
    strict_fresh_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    v1_reference_rows: list[dict[str, Any]] | None = None,
    v0_reference_rows: list[dict[str, Any]] | None = None,
    target_pair_count: int = 4,
    fresh_list_degraded: bool = False,
) -> dict[str, Any]:
    summary = protocol_v2_batch_outcomes_summary(
        strict_fresh_rows=strict_fresh_rows,
        reference_rows=reference_rows,
        v1_reference_rows=v1_reference_rows,
        v0_reference_rows=v0_reference_rows,
        target_pair_count=target_pair_count,
        fresh_list_degraded=fresh_list_degraded,
    )
    gates = protocol_v2_batch_outcomes_gates(strict_fresh_rows, reference_rows, summary)
    return generate_report(
        phase=PROTOCOL_V2_BATCH_OUTCOMES_PHASE,
        decision=protocol_v2_batch_outcomes_decision(summary, gates),
        gate_results=gates,
        extras={
            "version": PROTOCOL_V2_BATCH_OUTCOMES_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "summary": summary,
            "continuation_policy": protocol_v2_batch_outcomes_continuation_policy(
                summary,
                gates,
            ),
        },
    )


def write_protocol_v2_batch_outcomes_evidence(
    *,
    root: Path,
    output_dir: Path,
    include_v1_reference: bool = True,
    include_v0_reference: bool = True,
    target_pair_count: int = 4,
) -> dict[str, Any]:
    """Aggregate Protocol v2 official outcomes and write a fully stratified evidence package."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # locate fresh candidate list; if missing, degrade gracefully
    fresh_list_path = _find_fresh_list_path(root)
    fresh_list_degraded = not fresh_list_path.is_file()
    fresh_task_ids = _load_fresh_candidate_task_ids(fresh_list_path)

    # load v2 pairs and split by stratum
    all_v2_rows = load_protocol_v2_outcome_rows(root, fresh_task_ids=fresh_task_ids)
    strict_fresh_rows = [r for r in all_v2_rows if r["lineage"] == "v2_strict_fresh"]
    reference_rows = [r for r in all_v2_rows if r["lineage"] == "v2_reference"]

    v1_reference_rows = load_v1_reference_outcome_rows(root) if include_v1_reference else []
    v0_reference_rows = load_v0_reference_outcome_rows(root) if include_v0_reference else []

    report = protocol_v2_batch_outcomes_report(
        strict_fresh_rows=strict_fresh_rows,
        reference_rows=reference_rows,
        v1_reference_rows=v1_reference_rows,
        v0_reference_rows=v0_reference_rows,
        target_pair_count=target_pair_count,
        fresh_list_degraded=fresh_list_degraded,
    )

    # Oracle-probe layer (contaminated by design): listed explicitly, excluded
    # from every stratum count above. Lazy import keeps module load light.
    from wutai_clinic.intervention.oracle_capsule import load_oracle_probe_rows

    oracle_rows = load_oracle_probe_rows(root)
    report["oracle_probe_rows_excluded"] = oracle_rows
    report["summary"]["oracle_probe_pair_count_excluded"] = len(oracle_rows)

    # output paths
    pairs_path = output_dir / "protocol_v2_batch_outcomes_pairs.jsonl"
    ref_v2_path = output_dir / "protocol_v2_batch_outcomes_reference_pairs.jsonl"
    ref_v1_path = output_dir / "protocol_v2_batch_outcomes_v1_reference_pairs.jsonl"
    ref_v0_path = output_dir / "protocol_v2_batch_outcomes_v0_reference_pairs.jsonl"
    report_path = output_dir / "protocol_v2_batch_outcomes_report.json"
    manifest_path = output_dir / "protocol_v2_batch_outcomes_manifest.json"

    write_jsonl(pairs_path, strict_fresh_rows)
    write_jsonl(ref_v2_path, reference_rows)
    write_jsonl(ref_v1_path, v1_reference_rows)
    write_jsonl(ref_v0_path, v0_reference_rows)
    _write_json(report_path, report)

    artifacts = [
        _artifact(p)
        for p in [pairs_path, ref_v2_path, ref_v1_path, ref_v0_path, report_path]
    ]
    artifacts.extend(_artifact(Path(r["scorecard_path"])) for r in all_v2_rows)
    artifacts.extend(_artifact(Path(r["scorecard_path"])) for r in v1_reference_rows)
    artifacts.extend(_artifact(Path(r["scorecard_path"])) for r in v0_reference_rows)

    manifest = generate_manifest(
        phase=PROTOCOL_V2_BATCH_OUTCOMES_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V2_BATCH_OUTCOMES_VERSION
    _write_json(manifest_path, manifest)

    return {
        "report": report,
        "manifest": manifest,
        "pairs_path": pairs_path,
        "ref_v2_path": ref_v2_path,
        "ref_v1_path": ref_v1_path,
        "ref_v0_path": ref_v0_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "CLAIM_BOUNDARY",
    "PROTOCOL_V2_BATCH_OUTCOMES_VERSION",
    "load_protocol_v2_outcome_rows",
    "load_v0_reference_outcome_rows",
    "load_v1_reference_outcome_rows",
    "protocol_v2_batch_outcomes_report",
    "write_protocol_v2_batch_outcomes_evidence",
]
