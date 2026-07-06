from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.runner import MODELS, relative, sha256_file
from wutai_clinic.io import read_jsonl, write_jsonl

PHASE315_EVAL_VERSION = "phase315_one_pair_official_eval_v1"
PHASE316_EVAL_VERSION = "phase316_batch_uncapped_official_eval_v2"
ONE_PAIR_MODEL_NAME_PREFIX = "phase315_one_pair_smoke"
ONE_PAIR_RUN_ID = "phase315_one_pair_official_eval"
DATASET_NAME = "SWE-bench/SWE-bench_Lite"
SPLIT = "test"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def one_pair_arm_prediction_path(prediction_dir: Path, arm_type: str) -> Path:
    return prediction_dir / f"{ONE_PAIR_MODEL_NAME_PREFIX}_{arm_type}.jsonl"


def official_report_path(eval_dir: Path, run_id: str, model_name: str, source_task_id: str) -> Path:
    return eval_dir / "logs/run_evaluation" / run_id / model_name / source_task_id / "report.json"


def load_one_pair_arm_inputs(
    smoke_plan_path: Path = MODELS / "phase315_one_pair_smoke_plan.jsonl",
    smoke_root: Path = MODELS / "phase315_one_pair_smoke_runs",
) -> list[dict[str, Any]]:
    rows = sorted(read_jsonl(smoke_plan_path), key=lambda row: str(row["arm_type"]))
    arms = []
    for row in rows:
        source_task_id = str(row["source_task_id"])
        arm_type = str(row["arm_type"])
        arm_dir = smoke_root / str(row["pair_id"]) / arm_type / source_task_id
        pred_path = arm_dir / f"{source_task_id}.pred"
        patch_path = arm_dir / f"{source_task_id}.patch"
        arms.append(
            {
                "phase": "3.15",
                "eval_version": PHASE315_EVAL_VERSION,
                "pair_id": row["pair_id"],
                "arm_id": row["arm_id"],
                "arm_type": arm_type,
                "source_task_id": source_task_id,
                "expected_model_name": f"{ONE_PAIR_MODEL_NAME_PREFIX}_{arm_type}",
                "smoke_pred_path": pred_path,
                "smoke_patch_path": patch_path,
                "smoke_pred_exists": pred_path.is_file(),
                "smoke_patch_exists": patch_path.is_file(),
            }
        )
    return arms


def prediction_row_from_pred_file(
    pred_path: Path, *, model_name: str, source_task_id: str
) -> dict[str, str]:
    prediction = load_json(pred_path)
    model_patch = prediction.get("model_patch") or ""
    return {
        "model_name_or_path": model_name,
        "instance_id": source_task_id,
        "model_patch": model_patch,
    }


def write_one_pair_arm_prediction(arm: dict[str, Any], prediction_dir: Path) -> dict[str, Any]:
    output_path = one_pair_arm_prediction_path(prediction_dir, arm["arm_type"])
    row = prediction_row_from_pred_file(
        Path(arm["smoke_pred_path"]),
        model_name=arm["expected_model_name"],
        source_task_id=arm["source_task_id"],
    )
    write_jsonl(output_path, [row])
    return {
        "arm_type": arm["arm_type"],
        "source_task_id": arm["source_task_id"],
        "prediction_path": output_path,
        "prediction_sha256": sha256_file(output_path),
        "patch_sha256": stable_json_hash(row["model_patch"]),
        "patch_bytes": len(row["model_patch"].encode("utf-8")),
    }


def tests_status_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
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


def summarize_official_report(
    path: Path, source_task_id: str, *, empty_patch: bool = False
) -> dict[str, Any]:
    if empty_patch and not path.is_file():
        return {
            "completed": True,
            "empty_patch": True,
            "empty_patch_eval_short_circuit": True,
            "official_report_path": relative(path),
            "official_report_sha256": None,
            "resolved": False,
            "patch_successfully_applied": False,
            "tests_status_counts": {},
        }
    if not path.is_file():
        return {
            "completed": False,
            "empty_patch": empty_patch,
            "empty_patch_eval_short_circuit": False,
            "official_report_path": relative(path),
            "official_report_sha256": None,
            "resolved": None,
            "patch_successfully_applied": None,
            "tests_status_counts": {},
        }
    data = load_json(path)
    payload = data.get(source_task_id, {})
    return {
        "completed": bool(payload),
        "empty_patch": empty_patch,
        "empty_patch_eval_short_circuit": False,
        "official_report_path": relative(path),
        "official_report_sha256": sha256_file(path),
        "resolved": payload.get("resolved"),
        "patch_successfully_applied": payload.get("patch_successfully_applied"),
        "tests_status_counts": tests_status_counts(payload),
    }


def one_pair_arm_reports(
    *,
    smoke_plan_path: Path = MODELS / "phase315_one_pair_smoke_plan.jsonl",
    smoke_root: Path = MODELS / "phase315_one_pair_smoke_runs",
    prediction_dir: Path = MODELS / "phase315_one_pair_official_eval_predictions",
    eval_dir: Path = MODELS / "phase315_one_pair_official_eval",
    run_id: str = ONE_PAIR_RUN_ID,
    write_predictions: bool = True,
) -> list[dict[str, Any]]:
    arms = load_one_pair_arm_inputs(smoke_plan_path, smoke_root)
    prediction_rows = [
        write_one_pair_arm_prediction(arm, prediction_dir)
        if write_predictions
        else {
            "arm_type": arm["arm_type"],
            "source_task_id": arm["source_task_id"],
            "prediction_path": one_pair_arm_prediction_path(prediction_dir, arm["arm_type"]),
            "prediction_sha256": sha256_file(
                one_pair_arm_prediction_path(prediction_dir, arm["arm_type"])
            ),
            "patch_sha256": stable_json_hash(
                prediction_row_from_pred_file(
                    Path(arm["smoke_pred_path"]),
                    model_name=arm["expected_model_name"],
                    source_task_id=arm["source_task_id"],
                )["model_patch"]
            ),
            "patch_bytes": len(
                prediction_row_from_pred_file(
                    Path(arm["smoke_pred_path"]),
                    model_name=arm["expected_model_name"],
                    source_task_id=arm["source_task_id"],
                )["model_patch"].encode("utf-8")
            ),
        }
        for arm in arms
    ]
    prediction_by_arm = {row["arm_type"]: row for row in prediction_rows}
    reports = []
    for arm in arms:
        pred_summary = prediction_by_arm[arm["arm_type"]]
        report_path = official_report_path(
            eval_dir, run_id, arm["expected_model_name"], arm["source_task_id"]
        )
        official_summary = summarize_official_report(
            report_path,
            arm["source_task_id"],
            empty_patch=pred_summary["patch_bytes"] == 0,
        )
        official_summary.pop("empty_patch", None)
        official_summary.pop("empty_patch_eval_short_circuit", None)
        reports.append(
            {
                "arm_id": arm["arm_id"],
                "arm_type": arm["arm_type"],
                "pair_id": arm["pair_id"],
                "source_task_id": arm["source_task_id"],
                "model_name_or_path": arm["expected_model_name"],
                "prediction_path": relative(Path(pred_summary["prediction_path"])),
                "prediction_sha256": pred_summary["prediction_sha256"],
                "patch_sha256": pred_summary["patch_sha256"],
                "patch_bytes": pred_summary["patch_bytes"],
                **official_summary,
            }
        )
    return reports


def effect_summary(arm_reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {row["arm_type"]: row for row in arm_reports}
    control = by_arm.get("control", {}).get("resolved")
    intervention = by_arm.get("intervention", {}).get("resolved")
    if control is None or intervention is None:
        label = "pending_or_incomplete"
    elif control is True and intervention is True:
        label = "both_resolved_single_pair_no_uplift_claim"
    elif control is False and intervention is True:
        label = "intervention_only_resolved_single_pair_candidate"
    elif control is True and intervention is False:
        label = "control_only_resolved_single_pair_negative_candidate"
    else:
        label = "both_unresolved_single_pair_no_uplift"
    return {
        "control_resolved": control,
        "intervention_resolved": intervention,
        "single_pair_effect_label": label,
        "claim_boundary": "Single-pair official eval is not Phase 3.16 paired uplift evidence.",
    }


def evaluate(
    predictions: Mapping[str, str],
    dataset: str = "swe-bench-lite",
    *,
    official_reports: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    if official_reports is not None:
        return {
            instance_id: bool(official_reports.get(instance_id, False))
            for instance_id in predictions
        }
    return {instance_id: bool(patch.strip()) for instance_id, patch in predictions.items()}
