from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live_pair import SWEAgentLivePairSpec, run_sweagent_live_pair
from wutai_clinic.adapters.sweagent_official_pair import (
    SWEAgentOfficialPairSpec,
    run_sweagent_official_pair,
)
from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PHASE6_OFFICIAL_EVAL_PHASE = "6.sweagent_live_pair_official_eval"
SWEAGENT_PHASE6_OFFICIAL_EVAL_VERSION = "phase6_sweagent_live_pair_official_eval_v1"
SWEAGENT_PHASE6_OFFICIAL_EVAL_BOUNDARY = (
    "This package turns one Phase 5 SWE-agent live pair into an outcome-backed official "
    "SWE-bench attribution artifact. It archives patches, writes per-arm predictions, "
    "optionally invokes the official eval harness in a separate eval directory, and only "
    "emits a resolved/unresolved effect label when both official arm reports are present."
)
READY_PREFLIGHT_DECISION = "sweagent_live_hook_preflight_pair_ready_pending_official_eval"
READY_PAIR_DECISION = "sweagent_live_pair_ready_pending_official_eval"
DEFAULT_DATASET_NAME = "SWE-bench/SWE-bench_Lite"
DEFAULT_SPLIT = "test"
DEFAULT_RUN_ID = "phase6_sweagent_live_pair_official_eval"


@dataclass(frozen=True)
class SWEAgentPhase6OfficialEvalSpec:
    live_preflight_dir: Path
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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


def _safe_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return cleaned.strip("._") or "unknown"


def _arm_model_name(arm_type: str, pair_id: str | None) -> str:
    suffix = _safe_component(pair_id or "single_pair")
    return f"phase6_official_eval__{suffix}__{arm_type}"


def _prediction_path(prediction_dir: Path, arm_type: str, source_task_id: str) -> Path:
    return prediction_dir / f"{arm_type}_{_safe_component(source_task_id)}.jsonl"


def _official_report_path(
    *,
    eval_dir: Path,
    run_id: str,
    model_name: str,
    source_task_id: str,
) -> Path:
    return eval_dir / "logs/run_evaluation" / run_id / model_name / source_task_id / "report.json"


def _default_compat_wrapper() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "models" / "run_phase310_swebench_compat.py"
        if candidate.is_file():
            return candidate
    return None


def _is_same_or_child(path: Path, parent: Path) -> bool:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    return resolved_path == resolved_parent or resolved_parent in resolved_path.parents


def _read_live_pair_summary(pair_summary_path: Path) -> dict[str, Any]:
    rows = list(read_jsonl(pair_summary_path)) if pair_summary_path.is_file() else []
    return rows[0] if rows else {}


def _patch_info(arm_dir: Path, arm_report: dict[str, Any]) -> dict[str, Any]:
    patch_path = arm_dir / "sweagent_live_single.patch"
    expected_sha = arm_report.get("patch_archive_sha256")
    actual_sha = sha256_file(patch_path) if patch_path.is_file() else None
    return {
        "path": patch_path,
        "exists": patch_path.is_file(),
        "sha256": actual_sha,
        "sha_matches_live_report": expected_sha in {None, actual_sha},
        "bytes": patch_path.stat().st_size if patch_path.is_file() else 0,
    }


def _write_prediction(
    *,
    patch_path: Path,
    prediction_path: Path,
    source_task_id: str,
    model_name: str,
) -> dict[str, Any]:
    model_patch = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
    row = {
        "instance_id": source_task_id,
        "model_name_or_path": model_name,
        "model_patch": model_patch,
    }
    write_jsonl(prediction_path, [row])
    return {
        "prediction_path": prediction_path,
        "prediction_sha256": sha256_file(prediction_path),
        "patch_sha256": stable_json_hash(model_patch),
        "patch_bytes": len(model_patch.encode("utf-8")),
    }


def _tests_status_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    status = payload.get("tests_status")
    if not isinstance(status, dict):
        return {}
    counts: dict[str, dict[str, int]] = {}
    for family, result in status.items():
        if not isinstance(result, dict):
            continue
        success = result.get("success") if isinstance(result.get("success"), list) else []
        failure = result.get("failure") if isinstance(result.get("failure"), list) else []
        counts[str(family)] = {
            "success_count": len(success),
            "failure_count": len(failure),
        }
    return counts


def _summarize_official_report(path: Path, source_task_id: str) -> dict[str, Any]:
    if not path.is_file():
        return {
            "completed": False,
            "official_report_path": path.as_posix(),
            "official_report_sha256": None,
            "resolved": None,
            "patch_successfully_applied": None,
            "tests_status_counts": {},
        }
    data = _load_json(path)
    payload = data.get(source_task_id, {})
    return {
        "completed": bool(payload),
        "official_report_path": path.as_posix(),
        "official_report_sha256": sha256_file(path),
        "resolved": payload.get("resolved"),
        "patch_successfully_applied": payload.get("patch_successfully_applied"),
        "tests_status_counts": _tests_status_counts(payload) if isinstance(payload, dict) else {},
    }


def _run_official_eval(
    *,
    spec: SWEAgentPhase6OfficialEvalSpec,
    arm_type: str,
    prediction_path: Path,
    eval_dir: Path,
    model_name: str,
    source_task_id: str,
) -> dict[str, Any]:
    command = [sys.executable]
    compat_wrapper = _default_compat_wrapper()
    if spec.build_compat == "legacy-python-packaging" and compat_wrapper is not None:
        command.append(compat_wrapper.as_posix())
    else:
        command.extend(["-m", "swebench.harness.run_evaluation"])
    command.extend(
        [
            "-d",
            spec.dataset_name,
            "-s",
            spec.split,
            "-p",
            prediction_path.resolve().as_posix(),
            "--max_workers",
            str(spec.max_workers),
            "--timeout",
            str(spec.timeout),
            "--cache_level",
            "env",
            "--clean",
            "false",
            "-id",
            spec.run_id,
            "-n",
            "none",
            "--report_dir",
            eval_dir.resolve().as_posix(),
            "-i",
            source_task_id,
        ]
    )
    eval_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=eval_dir, check=False)
    return {
        "arm_type": arm_type,
        "source_task_id": source_task_id,
        "model_name_or_path": model_name,
        "returncode": result.returncode,
        "command_sha256": stable_json_hash(command),
        "command_preview": [
            "python",
            "Wutai_observatory/models/run_phase310_swebench_compat.py"
            if spec.build_compat == "legacy-python-packaging" and compat_wrapper is not None
            else "-m swebench.harness.run_evaluation",
            "-i",
            source_task_id,
            "-p",
            prediction_path.as_posix(),
        ],
    }


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


def _pair_summary_row(
    *,
    pair_id: str,
    source_task_id: str,
    control: dict[str, Any],
    treatment: dict[str, Any],
    live_pair_summary: dict[str, Any],
) -> dict[str, Any]:
    completed = bool(control["completed"] and treatment["completed"])
    patches_applied = bool(
        completed
        and control["patch_successfully_applied"] is True
        and treatment["patch_successfully_applied"] is True
    )
    treatment_injected_once = live_pair_summary.get("intervention_injected_once") is True
    effect_label = _effect_label(control["resolved"], treatment["resolved"])
    return {
        "pair_id": pair_id,
        "source_task_id": source_task_id,
        "pair_eval_scope": "main_treatment_attribution_candidate"
        if treatment_injected_once
        else "secondary_trigger_miss_audit_only",
        "official_eval_completed": completed,
        "patches_applied": patches_applied,
        "intervention_treatment_status": "treated_injected_once"
        if treatment_injected_once
        else "not_treated_trigger_miss",
        "intervention_injected_once": treatment_injected_once,
        "control_resolved": control["resolved"],
        "intervention_resolved": treatment["resolved"],
        "effect_label": effect_label,
        "main_attribution_eligible": bool(
            completed and patches_applied and treatment_injected_once
        ),
        "outcome_source": "official_eval" if completed else "pending_official_eval",
        "single_pair_only": True,
    }


def run_sweagent_phase6_official_eval(
    *,
    spec: SWEAgentPhase6OfficialEvalSpec,
    policy: RuntimePermissionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RuntimePermissionPolicy()
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    eval_dir = spec.eval_dir or (spec.output_dir / "official_eval")
    prediction_dir = spec.output_dir / "predictions"
    prediction_dir.mkdir(parents=True, exist_ok=True)

    preflight_report_path = spec.live_preflight_dir / "live_hook_preflight_report.json"
    pair_dir = spec.live_preflight_dir / "pair"
    control_dir = spec.live_preflight_dir / "control"
    treatment_dir = spec.live_preflight_dir / "treatment"
    pair_report_path = pair_dir / "sweagent_live_pair_report.json"
    pair_summary_path = pair_dir / "sweagent_live_pair_summary.jsonl"
    control_report_path = control_dir / "sweagent_live_single_report.json"
    treatment_report_path = treatment_dir / "sweagent_live_single_report.json"

    preflight_report = _load_json(preflight_report_path) if preflight_report_path.is_file() else {}
    pair_report = _load_json(pair_report_path) if pair_report_path.is_file() else {}
    live_pair_summary = _read_live_pair_summary(pair_summary_path)
    control_report = _load_json(control_report_path) if control_report_path.is_file() else {}
    treatment_report = _load_json(treatment_report_path) if treatment_report_path.is_file() else {}

    pair_id = (
        spec.pair_id
        or preflight_report.get("pair_id")
        or live_pair_summary.get("pair_id")
        or "phase6_single_pair"
    )
    source_task_id = (
        preflight_report.get("source_task_id")
        or live_pair_summary.get("source_task_id")
        or control_report.get("source_task_id")
        or treatment_report.get("source_task_id")
    )
    source_task_id = str(source_task_id) if source_task_id else None
    control_patch = _patch_info(control_dir, control_report)
    treatment_patch = _patch_info(treatment_dir, treatment_report)

    prediction_rows: dict[str, dict[str, Any]] = {}
    if source_task_id and control_patch["exists"] and treatment_patch["exists"]:
        for arm_type, patch in [("control", control_patch), ("intervention", treatment_patch)]:
            model_name = _arm_model_name(arm_type, str(pair_id))
            prediction_rows[arm_type] = {
                "arm_type": arm_type,
                "source_task_id": source_task_id,
                "model_name_or_path": model_name,
                **_write_prediction(
                    patch_path=patch["path"],
                    prediction_path=_prediction_path(prediction_dir, arm_type, source_task_id),
                    source_task_id=source_task_id,
                    model_name=model_name,
                ),
            }

    eval_isolated = not any(
        _is_same_or_child(eval_dir, live_dir) for live_dir in [control_dir, treatment_dir, pair_dir]
    )
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
        official_summary = _summarize_official_report(report_path, source_task_id)
        arm_reports.append(
            {
                "arm_type": arm_type,
                "pair_id": str(pair_id),
                "source_task_id": source_task_id,
                "model_name_or_path": row["model_name_or_path"],
                "prediction_path": row["prediction_path"].as_posix(),
                "prediction_sha256": row["prediction_sha256"],
                "patch_sha256": row["patch_sha256"],
                "patch_bytes": row["patch_bytes"],
                "patch_archive_path": (
                    control_patch["path"].as_posix()
                    if arm_type == "control"
                    else treatment_patch["path"].as_posix()
                ),
                "patch_archive_sha256": (
                    control_patch["sha256"] if arm_type == "control" else treatment_patch["sha256"]
                ),
                **official_summary,
            }
        )

    by_arm = {row["arm_type"]: row for row in arm_reports}
    control_arm = by_arm.get("control")
    treatment_arm = by_arm.get("intervention")
    official_completed = bool(
        control_arm
        and treatment_arm
        and control_arm["completed"] is True
        and treatment_arm["completed"] is True
    )
    pair_summary_rows = []
    if source_task_id and control_arm and treatment_arm:
        pair_summary_rows.append(
            _pair_summary_row(
                pair_id=str(pair_id),
                source_task_id=source_task_id,
                control=control_arm,
                treatment=treatment_arm,
                live_pair_summary=live_pair_summary,
            )
        )

    official_report_path = spec.output_dir / "phase6_official_eval_report.json"
    pair_summary_output_path = spec.output_dir / "phase6_official_eval_pair_summary.jsonl"
    final_pair_result = None
    official_pair_result = None
    gates = {
        "live_preflight_report_exists": preflight_report_path.is_file(),
        "live_preflight_ready_pending_official_eval": (
            preflight_report.get("decision") == READY_PREFLIGHT_DECISION
            and preflight_report.get("passed") is True
        ),
        "live_pair_report_exists": pair_report_path.is_file(),
        "live_pair_ready_pending_official_eval": (
            pair_report.get("decision") == READY_PAIR_DECISION and pair_report.get("passed") is True
        ),
        "source_task_id_present": source_task_id is not None,
        "control_patch_archive_present": control_patch["exists"],
        "treatment_patch_archive_present": treatment_patch["exists"],
        "patch_archive_hashes_match_live_reports": control_patch["sha_matches_live_report"]
        and treatment_patch["sha_matches_live_report"],
        "prediction_files_written": len(prediction_rows) == 2,
        "official_eval_ack_if_run_or_import": (
            not (spec.run_official_eval or official_completed) or policy.allow_official_eval
        ),
        "official_eval_isolated_from_live_generation": eval_isolated,
        "official_eval_returncode_zero_if_run": not spec.run_official_eval
        or all(result["returncode"] == 0 for result in run_results),
        "state_capsule_equivalence_preserved": (
            pair_report.get("fork_equivalence", {}).get("passed") is True
        ),
        "single_pair_only": True,
        "generalized_uplift_claim_not_made": True,
    }
    structural_passed = all(gates.values())
    if official_completed and structural_passed and control_arm and treatment_arm:
        final_pair_result = run_sweagent_live_pair(
            spec=SWEAgentLivePairSpec(
                control_dir=control_dir,
                treatment_dir=treatment_dir,
                output_dir=spec.output_dir / "final_live_pair",
                control_resolved=control_arm["resolved"],
                treatment_resolved=treatment_arm["resolved"],
                outcome_source="official_eval",
            ),
            policy=RuntimePermissionPolicy(allow_official_eval=True),
        )
    if not structural_passed:
        decision = "phase6_official_eval_blocked"
    elif (
        official_completed
        and final_pair_result
        and final_pair_result["report"].get("passed") is True
    ):
        decision = "phase6_official_eval_outcome_label_ready"
    elif spec.run_official_eval and run_results:
        decision = "phase6_official_eval_pending_or_failed"
    else:
        decision = "phase6_official_eval_ready_pending_official_eval"

    effect_label = (
        final_pair_result["report"].get("effect_label")
        if final_pair_result is not None
        else pair_summary_rows[0]["effect_label"]
        if pair_summary_rows
        else "pending_or_incomplete"
    )
    dual_scorecard = {
        "pair_id": str(pair_id),
        "source_task_id": source_task_id,
        "control_resolved": control_arm.get("resolved") if control_arm else None,
        "treatment_resolved": treatment_arm.get("resolved") if treatment_arm else None,
        "effect_label": effect_label,
        "official_eval_completed": official_completed,
        "state_capsule_equivalent": pair_report.get("fork_equivalence", {}).get("passed") is True,
        "intervention_injected_once": live_pair_summary.get("intervention_injected_once") is True,
        "outcome_source": "official_eval" if official_completed else "pending_official_eval",
        "single_pair_only": True,
    }
    dual_scorecard_path = spec.output_dir / "phase6_dual_scorecard.json"

    report = generate_report(
        phase=SWEAGENT_PHASE6_OFFICIAL_EVAL_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_PHASE6_OFFICIAL_EVAL_VERSION,
            "claim_boundary": SWEAGENT_PHASE6_OFFICIAL_EVAL_BOUNDARY,
            "live_preflight_dir": spec.live_preflight_dir.as_posix(),
            "eval_dir": eval_dir.as_posix(),
            "run_id": spec.run_id,
            "dataset_name": spec.dataset_name,
            "split": spec.split,
            "official_eval_started": spec.run_official_eval or official_completed,
            "official_eval_started_by_this_command": spec.run_official_eval,
            "official_eval_completed": official_completed,
            "pair_id": str(pair_id),
            "source_task_id": source_task_id,
            "arm_reports": arm_reports,
            "run_results": run_results,
            "effect_label": effect_label,
            "final_live_pair_report": final_pair_result["report_path"].as_posix()
            if final_pair_result
            else None,
            "final_live_pair_manifest": final_pair_result["manifest_path"].as_posix()
            if final_pair_result
            else None,
            "pair_summary_path": pair_summary_output_path.as_posix(),
            "dual_scorecard_path": dual_scorecard_path.as_posix(),
        },
    )
    _write_json(official_report_path, report)
    write_jsonl(pair_summary_output_path, pair_summary_rows)
    _write_json(dual_scorecard_path, dual_scorecard)

    if official_completed and structural_passed:
        official_pair_result = run_sweagent_official_pair(
            spec=SWEAgentOfficialPairSpec(
                pair_summary_path=pair_summary_output_path,
                official_eval_report=official_report_path,
                output_dir=spec.output_dir / "official_pair",
                pair_id=str(pair_id),
            ),
            policy=RuntimePermissionPolicy(allow_official_eval=True),
        )

    artifact_paths = [
        preflight_report_path,
        pair_report_path,
        pair_summary_path,
        control_report_path,
        treatment_report_path,
        control_patch["path"],
        treatment_patch["path"],
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
    if final_pair_result is not None:
        artifact_paths.extend(
            [
                final_pair_result["report_path"],
                final_pair_result["manifest_path"],
                final_pair_result["summary_path"],
            ]
        )
    if official_pair_result is not None:
        artifact_paths.extend(
            [
                official_pair_result["report_path"],
                official_pair_result["manifest_path"],
                official_pair_result["summary_path"],
            ]
        )
    artifacts = [_artifact(path) for path in artifact_paths]
    manifest = generate_manifest(
        phase=SWEAGENT_PHASE6_OFFICIAL_EVAL_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_PHASE6_OFFICIAL_EVAL_VERSION
    manifest["determinism_summary_sha256"] = stable_json_hash(
        {
            "decision": decision,
            "pair_id": str(pair_id),
            "source_task_id": source_task_id,
            "arm_reports": arm_reports,
            "dual_scorecard": dual_scorecard,
        }
    )
    manifest_path = spec.output_dir / "phase6_official_eval_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "dual_scorecard": dual_scorecard,
        "report_path": official_report_path,
        "manifest_path": manifest_path,
        "pair_summary_path": pair_summary_output_path,
        "dual_scorecard_path": dual_scorecard_path,
        "final_pair_result": final_pair_result,
        "official_pair_result": official_pair_result,
    }


__all__ = [
    "SWEAGENT_PHASE6_OFFICIAL_EVAL_PHASE",
    "SWEAGENT_PHASE6_OFFICIAL_EVAL_VERSION",
    "SWEAgentPhase6OfficialEvalSpec",
    "run_sweagent_phase6_official_eval",
]
