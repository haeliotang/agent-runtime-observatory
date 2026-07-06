from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.intervention.evaluator import (
    effect_summary,
    evaluate,
    one_pair_arm_reports,
    prediction_row_from_pred_file,
    summarize_official_report,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_phase315_one_pair_arm_reports_match_frozen_resolved_results() -> None:
    expected_report = json.loads(
        (MODELS / "phase315_one_pair_official_eval_report.json").read_text()
    )
    expected_by_arm = {row["arm_type"]: row for row in expected_report["arm_reports"]}

    reports = one_pair_arm_reports(write_predictions=False)
    reports_by_arm = {row["arm_type"]: row for row in reports}

    assert reports_by_arm["control"]["resolved"] is False
    assert reports_by_arm["intervention"]["resolved"] is True
    for arm_type, expected in expected_by_arm.items():
        actual = reports_by_arm[arm_type]
        for key in [
            "arm_id",
            "source_task_id",
            "prediction_sha256",
            "patch_sha256",
            "patch_bytes",
            "official_report_sha256",
            "resolved",
            "patch_successfully_applied",
            "tests_status_counts",
        ]:
            assert actual[key] == expected[key]


def test_phase315_effect_summary_matches_frozen_report() -> None:
    expected_report = json.loads(
        (MODELS / "phase315_one_pair_official_eval_report.json").read_text()
    )
    reports = one_pair_arm_reports(write_predictions=False)

    assert effect_summary(reports) == expected_report["effect_summary"]


def test_prediction_row_from_pred_file_handles_empty_patch(tmp_path: Path) -> None:
    pred_path = tmp_path / "empty.pred"
    pred_path.write_text(json.dumps({"model_patch": ""}), encoding="utf-8")

    row = prediction_row_from_pred_file(pred_path, model_name="model", source_task_id="task")

    assert row == {"model_name_or_path": "model", "instance_id": "task", "model_patch": ""}
    assert evaluate({"task": row["model_patch"]}) == {"task": False}


def test_summarize_official_report_short_circuits_missing_empty_patch(tmp_path: Path) -> None:
    report = summarize_official_report(tmp_path / "missing.json", "task", empty_patch=True)

    assert report["completed"] is True
    assert report["empty_patch_eval_short_circuit"] is True
    assert report["resolved"] is False
    assert report["patch_successfully_applied"] is False
