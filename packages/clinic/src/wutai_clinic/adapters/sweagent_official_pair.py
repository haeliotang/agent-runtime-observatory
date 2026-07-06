from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_OFFICIAL_PAIR_PHASE = "5.sweagent_official_pair_outcome"
SWEAGENT_OFFICIAL_PAIR_VERSION = "phase5_sweagent_official_pair_outcome_v1"
SWEAGENT_OFFICIAL_PAIR_BOUNDARY = (
    "This package imports one completed SWE-bench official-eval pair and exposes its "
    "resolved/unresolved outcome as an auditable single-pair artifact. It does not start "
    "Docker, call a provider, rerun official eval, claim state-capsule equivalence, or make "
    "a generalized uplift claim."
)


@dataclass(frozen=True)
class SWEAgentOfficialPairSpec:
    pair_summary_path: Path
    official_eval_report: Path
    output_dir: Path
    pair_id: str | None = None
    require_main_attribution: bool = True


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(raw_path: str, *, base: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, base / path, base.parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


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


def _hash_matches(path: Path, expected: str | None) -> bool:
    if expected is None:
        return False
    return path.is_file() and sha256_file(path) == expected


def _select_pair_row(rows: list[dict[str, Any]], pair_id: str | None) -> dict[str, Any] | None:
    if pair_id:
        return next((row for row in rows if row.get("pair_id") == pair_id), None)
    return next(
        (
            row
            for row in rows
            if row.get("pair_eval_scope") == "main_treatment_attribution_candidate"
            and row.get("main_attribution_eligible") is True
            and row.get("official_eval_completed") is True
        ),
        rows[0] if rows else None,
    )


def _arm_reports_for_pair(report: dict[str, Any], pair_id: str | None) -> list[dict[str, Any]]:
    arm_reports = report.get("arm_reports")
    if not isinstance(arm_reports, list):
        return []
    return [arm for arm in arm_reports if arm.get("pair_id") == pair_id]


def _effect_label(control_resolved: bool | None, treatment_resolved: bool | None) -> str:
    if control_resolved is None or treatment_resolved is None:
        return "pending_or_incomplete"
    if control_resolved and treatment_resolved:
        return "both_resolved_trigger_hit_pair_no_uplift"
    if not control_resolved and treatment_resolved:
        return "intervention_only_resolved_trigger_hit_candidate"
    if control_resolved and not treatment_resolved:
        return "control_only_resolved_trigger_hit_negative_candidate"
    return "both_unresolved_trigger_hit_pair_no_uplift"


def run_sweagent_official_pair(
    *,
    spec: SWEAgentOfficialPairSpec,
    policy: RuntimePermissionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RuntimePermissionPolicy()
    spec.output_dir.mkdir(parents=True, exist_ok=True)

    pair_rows = list(read_jsonl(spec.pair_summary_path)) if spec.pair_summary_path.is_file() else []
    official_report = (
        _load_json(spec.official_eval_report) if spec.official_eval_report.is_file() else {}
    )
    pair_row = _select_pair_row(pair_rows, spec.pair_id)
    pair_id = str(pair_row.get("pair_id")) if pair_row else spec.pair_id
    arm_reports = _arm_reports_for_pair(official_report, pair_id)
    by_arm = {str(arm.get("arm_type")): arm for arm in arm_reports}
    control = by_arm.get("control")
    treatment = by_arm.get("intervention") or by_arm.get("treatment")
    arm_source_ids = {
        str(arm.get("source_task_id")) for arm in [control, treatment] if arm is not None
    }
    source_task_id = next(iter(arm_source_ids)) if len(arm_source_ids) == 1 else None
    control_resolved = control.get("resolved") if control else None
    treatment_resolved = treatment.get("resolved") if treatment else None
    computed_effect_label = _effect_label(control_resolved, treatment_resolved)
    pair_effect_label = str(pair_row.get("effect_label")) if pair_row else computed_effect_label

    base = spec.official_eval_report.parent
    arm_artifact_paths = []
    official_report_hashes_match = True
    prediction_hashes_match = True
    for arm in [arm for arm in [control, treatment] if arm is not None]:
        official_path = _resolve_path(str(arm.get("official_report_path") or ""), base=base)
        prediction_path = _resolve_path(str(arm.get("prediction_path") or ""), base=base)
        arm_artifact_paths.extend([official_path, prediction_path])
        official_report_hashes_match = official_report_hashes_match and _hash_matches(
            official_path, arm.get("official_report_sha256")
        )
        prediction_hashes_match = prediction_hashes_match and _hash_matches(
            prediction_path, arm.get("prediction_sha256")
        )

    pair_row_matches_outcome = bool(
        pair_row
        and pair_row.get("control_resolved") is control_resolved
        and pair_row.get("intervention_resolved") is treatment_resolved
    )
    main_attribution_eligible = bool(pair_row and pair_row.get("main_attribution_eligible") is True)
    gates = {
        "official_eval_acknowledged": policy.allow_official_eval,
        "pair_summary_exists": spec.pair_summary_path.is_file(),
        "official_eval_report_exists": spec.official_eval_report.is_file(),
        "official_eval_report_passed": official_report.get("passed") is True,
        "official_eval_started": official_report.get("official_eval_started") is True,
        "pair_summary_row_found": pair_row is not None,
        "two_arm_reports_for_pair": len(arm_reports) == 2,
        "control_and_intervention_present": control is not None and treatment is not None,
        "same_source_task_id": source_task_id is not None,
        "official_eval_completed_for_pair": all(
            arm.get("completed") is True for arm in [control, treatment] if arm is not None
        )
        and control is not None
        and treatment is not None,
        "resolved_booleans_present": isinstance(control_resolved, bool)
        and isinstance(treatment_resolved, bool),
        "pair_row_matches_arm_outcomes": pair_row_matches_outcome,
        "computed_effect_matches_pair_summary": computed_effect_label == pair_effect_label,
        "patches_applied": all(
            arm.get("patch_successfully_applied") is True
            for arm in [control, treatment]
            if arm is not None
        )
        and control is not None
        and treatment is not None,
        "official_report_hashes_match": official_report_hashes_match,
        "prediction_hashes_match": prediction_hashes_match,
        "main_attribution_eligible_if_required": (not spec.require_main_attribution)
        or main_attribution_eligible,
        "state_capsule_equivalence_not_claimed": True,
        "single_pair_only": True,
        "generalized_uplift_claim_not_made": True,
    }
    decision = (
        "sweagent_official_pair_outcome_label_ready"
        if all(gates.values())
        else "sweagent_official_pair_blocked"
    )
    output_summary = {
        **(pair_row or {}),
        "pair_id": pair_id,
        "source_task_id": source_task_id,
        "control_resolved": control_resolved,
        "intervention_resolved": treatment_resolved,
        "effect_label": pair_effect_label,
        "official_eval_completed": gates["official_eval_completed_for_pair"],
        "official_eval_report": spec.official_eval_report.as_posix(),
        "pair_summary_source": spec.pair_summary_path.as_posix(),
        "state_capsule_equivalence_claimed": False,
        "single_pair_only": True,
    }

    report_path = spec.output_dir / "sweagent_official_pair_report.json"
    summary_path = spec.output_dir / "sweagent_official_pair_summary.jsonl"
    manifest_path = spec.output_dir / "sweagent_official_pair_manifest.json"
    report = generate_report(
        phase=SWEAGENT_OFFICIAL_PAIR_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_OFFICIAL_PAIR_VERSION,
            "claim_boundary": SWEAGENT_OFFICIAL_PAIR_BOUNDARY,
            "pair_id": pair_id,
            "source_task_id": source_task_id,
            "control_resolved": control_resolved,
            "treatment_resolved": treatment_resolved,
            "effect_label": pair_effect_label,
            "computed_effect_label": computed_effect_label,
            "official_eval_report": spec.official_eval_report.as_posix(),
            "pair_summary_source": spec.pair_summary_path.as_posix(),
            "state_capsule_equivalence_claimed": False,
            "official_eval_started_by_this_command": False,
        },
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(summary_path, [output_summary])
    artifacts = [
        _artifact(path)
        for path in [
            spec.pair_summary_path,
            spec.official_eval_report,
            *arm_artifact_paths,
            report_path,
            summary_path,
        ]
    ]
    manifest = generate_manifest(
        phase=SWEAGENT_OFFICIAL_PAIR_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_OFFICIAL_PAIR_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "pair_summary": output_summary,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "summary_path": summary_path,
    }


__all__ = [
    "SWEAGENT_OFFICIAL_PAIR_PHASE",
    "SWEAGENT_OFFICIAL_PAIR_VERSION",
    "SWEAgentOfficialPairSpec",
    "run_sweagent_official_pair",
]
