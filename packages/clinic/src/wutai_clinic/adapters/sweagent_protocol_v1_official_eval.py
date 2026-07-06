from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_phase6_official_eval import (
    DEFAULT_DATASET_NAME,
    DEFAULT_SPLIT,
    _arm_model_name,
    _effect_label,
    _is_same_or_child,
    _official_report_path,
    _prediction_path,
    _run_official_eval,
    _summarize_official_report,
    _write_prediction,
)
from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_PHASE = "6.protocol_v1_sweagent_official_eval"
SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_VERSION = "phase6_protocol_v1_official_eval_v1"
DEFAULT_RUN_ID = "phase6_protocol_v1_official_eval"
BOUNDARY = (
    "This package turns one Protocol v1 live pair into an outcome-backed official "
    "SWE-bench attribution artifact. It writes predictions from archived patches, "
    "optionally invokes the official eval harness in an isolated directory, and only "
    "emits a resolved/unresolved effect label when both official arm reports are present."
)


@dataclass(frozen=True)
class SWEAgentProtocolV1OfficialEvalSpec:
    pair_dir: Path
    output_dir: Path
    eval_dir: Path | None = None
    run_official_eval: bool = False
    run_id: str = DEFAULT_RUN_ID
    dataset_name: str = DEFAULT_DATASET_NAME
    split: str = DEFAULT_SPLIT
    max_workers: int = 1
    timeout: int = 1800
    build_compat: str | None = "legacy-python-packaging"
    pair_id: str | None = None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _read_pair_summary(summary_path: Path) -> dict[str, Any]:
    rows = list(read_jsonl(summary_path)) if summary_path.is_file() else []
    return rows[0] if rows else {}


def _resolve_path(raw_path: str | None, *, base: Path) -> Path:
    if not raw_path:
        return base / "__missing__"
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, base / path, base.parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _pair_summary_row(
    *,
    pair_summary: dict[str, Any],
    control: dict[str, Any],
    treatment: dict[str, Any],
    effect_label: str,
    official_completed: bool,
) -> dict[str, Any]:
    patches_applied = bool(
        official_completed
        and control.get("patch_successfully_applied") is True
        and treatment.get("patch_successfully_applied") is True
    )
    return {
        **pair_summary,
        "control_resolved": control.get("resolved"),
        "intervention_resolved": treatment.get("resolved"),
        "effect_label": effect_label,
        "official_eval_completed": official_completed,
        "patches_applied": patches_applied,
        "outcome_source": "official_eval" if official_completed else "pending_official_eval",
        "state_capsule_equivalence_claimed": False,
        "single_pair_only": True,
    }


def run_sweagent_protocol_v1_official_eval(
    *,
    spec: SWEAgentProtocolV1OfficialEvalSpec,
    policy: RuntimePermissionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RuntimePermissionPolicy()
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    eval_dir = spec.eval_dir or (spec.output_dir / "official_eval")
    prediction_dir = spec.output_dir / "predictions"
    prediction_dir.mkdir(parents=True, exist_ok=True)

    pair_report_path = spec.pair_dir / "protocol_v1_live_pair_report.json"
    pair_summary_path = spec.pair_dir / "protocol_v1_live_pair_summary.jsonl"
    pair_report = _load_json(pair_report_path) if pair_report_path.is_file() else {}
    pair_summary = _read_pair_summary(pair_summary_path)
    pair_id = spec.pair_id or pair_report.get("pair_id") or pair_summary.get("pair_id")
    pair_id = str(pair_id or "protocol_v1_single_pair")
    source_task_id = pair_report.get("source_task_id") or pair_summary.get("source_task_id")
    source_task_id = str(source_task_id) if source_task_id else None
    control_patch = _resolve_path(
        pair_summary.get("control_patch_archive_path"), base=spec.pair_dir
    )
    treatment_patch = _resolve_path(
        pair_summary.get("intervention_patch_archive_path"),
        base=spec.pair_dir,
    )

    prediction_rows: dict[str, dict[str, Any]] = {}
    if source_task_id and control_patch.is_file() and treatment_patch.is_file():
        for arm_type, patch_path in [("control", control_patch), ("intervention", treatment_patch)]:
            model_name = _arm_model_name(arm_type, pair_id)
            prediction_rows[arm_type] = {
                "arm_type": arm_type,
                "source_task_id": source_task_id,
                "model_name_or_path": model_name,
                **_write_prediction(
                    patch_path=patch_path,
                    prediction_path=_prediction_path(prediction_dir, arm_type, source_task_id),
                    source_task_id=source_task_id,
                    model_name=model_name,
                ),
            }

    eval_isolated = not _is_same_or_child(eval_dir, spec.pair_dir)
    run_results = []
    official_eval_run_authorized = not spec.run_official_eval or policy.allow_official_eval
    if (
        spec.run_official_eval
        and official_eval_run_authorized
        and source_task_id
        and prediction_rows
    ):
        for arm_type, row in prediction_rows.items():
            run_results.append(
                _run_official_eval(
                    spec=spec,
                    arm_type=arm_type,
                    prediction_path=row["prediction_path"],
                    eval_dir=eval_dir,
                    model_name=row["model_name_or_path"],
                    source_task_id=source_task_id,
                )
            )

    arm_reports = []
    for arm_type in ["control", "intervention"]:
        row = prediction_rows.get(arm_type)
        if row is None or source_task_id is None:
            continue
        report_path = _official_report_path(
            eval_dir=eval_dir,
            run_id=spec.run_id,
            model_name=row["model_name_or_path"],
            source_task_id=source_task_id,
        )
        patch_path = control_patch if arm_type == "control" else treatment_patch
        arm_reports.append(
            {
                "arm_type": arm_type,
                "pair_id": pair_id,
                "source_task_id": source_task_id,
                "model_name_or_path": row["model_name_or_path"],
                "prediction_path": row["prediction_path"].as_posix(),
                "prediction_sha256": row["prediction_sha256"],
                "patch_sha256": row["patch_sha256"],
                "patch_bytes": row["patch_bytes"],
                "patch_archive_path": patch_path.as_posix(),
                "patch_archive_sha256": sha256_file(patch_path) if patch_path.is_file() else None,
                **_summarize_official_report(report_path, source_task_id),
            }
        )

    by_arm = {row["arm_type"]: row for row in arm_reports}
    control_arm = by_arm.get("control", {})
    treatment_arm = by_arm.get("intervention", {})
    official_completed = bool(
        control_arm.get("completed") is True and treatment_arm.get("completed") is True
    )
    effect_label = _effect_label(control_arm.get("resolved"), treatment_arm.get("resolved"))
    pair_summary_rows = []
    if source_task_id and control_arm and treatment_arm:
        pair_summary_rows.append(
            _pair_summary_row(
                pair_summary=pair_summary,
                control=control_arm,
                treatment=treatment_arm,
                effect_label=effect_label,
                official_completed=official_completed,
            )
        )

    official_report_path = spec.output_dir / "protocol_v1_official_eval_report.json"
    pair_summary_output_path = spec.output_dir / "protocol_v1_official_eval_pair_summary.jsonl"
    dual_scorecard_path = spec.output_dir / "protocol_v1_dual_scorecard.json"
    gates = {
        "protocol_v1_pair_report_exists": pair_report_path.is_file(),
        "protocol_v1_pair_ready_pending_official_eval": (
            pair_report.get("decision") == "protocol_v1_live_pair_ready_pending_official_eval"
            and pair_report.get("passed") is True
        ),
        "source_task_id_present": source_task_id is not None,
        "control_patch_archive_present": control_patch.is_file()
        and control_patch.stat().st_size > 0,
        "treatment_patch_archive_present": treatment_patch.is_file()
        and treatment_patch.stat().st_size > 0,
        "prediction_files_written": len(prediction_rows) == 2,
        "official_eval_ack_if_run_or_import": (
            not (spec.run_official_eval or official_completed) or policy.allow_official_eval
        ),
        "official_eval_isolated_from_live_generation": eval_isolated,
        "official_eval_returncode_zero_if_run": not spec.run_official_eval
        or all(result["returncode"] == 0 for result in run_results),
        "protocol_v1_pair_main_attribution_eligible": (
            pair_summary.get("main_attribution_eligible") is True
        ),
        "state_capsule_equivalence_not_claimed": (
            pair_report.get("state_capsule_equivalence_claimed") is False
        ),
        "single_pair_only": True,
        "generalized_uplift_claim_not_made": True,
    }
    structural_passed = all(gates.values())
    if not structural_passed:
        decision = "protocol_v1_official_eval_blocked"
    elif official_completed:
        decision = "protocol_v1_official_eval_outcome_label_ready"
    elif spec.run_official_eval and run_results:
        decision = "protocol_v1_official_eval_pending_or_failed"
    else:
        decision = "protocol_v1_official_eval_ready_pending_official_eval"

    dual_scorecard = {
        "pair_id": pair_id,
        "source_task_id": source_task_id,
        "control_resolved": control_arm.get("resolved"),
        "treatment_resolved": treatment_arm.get("resolved"),
        "effect_label": effect_label,
        "official_eval_completed": official_completed,
        "state_capsule_equivalence_claimed": False,
        "behavior_control_type": "protocol_v1_constraint_hook",
        "outcome_source": "official_eval" if official_completed else "pending_official_eval",
        "single_pair_only": True,
    }
    report = generate_report(
        phase=SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_VERSION,
            "claim_boundary": BOUNDARY,
            "pair_dir": spec.pair_dir.as_posix(),
            "eval_dir": eval_dir.as_posix(),
            "run_id": spec.run_id,
            "dataset_name": spec.dataset_name,
            "split": spec.split,
            "official_eval_started": spec.run_official_eval or official_completed,
            "official_eval_started_by_this_command": spec.run_official_eval,
            "official_eval_completed": official_completed,
            "pair_id": pair_id,
            "source_task_id": source_task_id,
            "arm_reports": arm_reports,
            "run_results": run_results,
            "effect_label": effect_label,
            "pair_summary_path": pair_summary_output_path.as_posix(),
            "dual_scorecard_path": dual_scorecard_path.as_posix(),
        },
    )
    _write_json(official_report_path, report)
    write_jsonl(pair_summary_output_path, pair_summary_rows)
    _write_json(dual_scorecard_path, dual_scorecard)
    artifacts = [
        _artifact(path)
        for path in [
            pair_report_path,
            pair_summary_path,
            control_patch,
            treatment_patch,
            *[row["prediction_path"] for row in prediction_rows.values()],
            *[
                Path(row["official_report_path"])
                for row in arm_reports
                if row.get("official_report_sha256")
            ],
            official_report_path,
            pair_summary_output_path,
            dual_scorecard_path,
        ]
    ]
    manifest = generate_manifest(
        phase=SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_VERSION
    manifest_path = spec.output_dir / "protocol_v1_official_eval_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "dual_scorecard": dual_scorecard,
        "report_path": official_report_path,
        "manifest_path": manifest_path,
        "pair_summary_path": pair_summary_output_path,
        "dual_scorecard_path": dual_scorecard_path,
    }


__all__ = [
    "SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_PHASE",
    "SWEAGENT_PROTOCOL_V1_OFFICIAL_EVAL_VERSION",
    "SWEAgentProtocolV1OfficialEvalSpec",
    "run_sweagent_protocol_v1_official_eval",
]
