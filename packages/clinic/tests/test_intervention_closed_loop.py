from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.intervention.closed_loop import (
    CLAIM_BOUNDARY,
    closed_loop_report,
    write_closed_loop_evidence,
)
from wutai_clinic.io import count_jsonl, read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def _candidate_rows() -> list[dict]:
    return list(read_jsonl(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"))


def _pair_summary_rows() -> list[dict]:
    return list(read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"))


def _batch2_pair_summary_rows() -> list[dict]:
    return list(read_jsonl(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"))


def test_closed_loop_report_gates_batch01_evidence() -> None:
    report, pairs, arms, attribution = closed_loop_report(
        candidate_rows=_candidate_rows(),
        pair_summary=_pair_summary_rows(),
    )

    assert len(pairs) == 32
    assert len(arms) == 64
    assert report["passed"] is True
    assert all(report["gates"].values())
    assert report["decision"] == "closed_loop_batchwise_continuation_ready"
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert attribution["classification_counts"] == {
        "main_treatment": 2,
        "trigger_miss": 2,
        "invalid": 0,
    }
    assert attribution["main_treatment_pairs"] == 2
    assert attribution["resolved_delta"] == 1


def test_closed_loop_report_gates_cumulative_batch1_batch2_evidence() -> None:
    cumulative_report = json.loads((MODELS / "phase316_cumulative_diagnosis_report.json").read_text())
    trigger_review = json.loads((MODELS / "phase316_trigger_policy_review_report.json").read_text())

    report, pairs, arms, attribution = closed_loop_report(
        candidate_rows=_candidate_rows(),
        pair_summary=[*_pair_summary_rows(), *_batch2_pair_summary_rows()],
        cumulative_report=cumulative_report,
        trigger_policy_review=trigger_review,
    )

    assert len(pairs) == 32
    assert len(arms) == 64
    assert report["passed"] is True
    assert all(report["gates"].values())
    assert report["decision"] == "closed_loop_trigger_policy_recalibration_required_before_batch3"
    assert attribution["completed_pairs"] == 8
    assert attribution["main_treatment_pairs"] == 2
    assert attribution["trigger_miss_pairs"] == 6
    assert attribution["resolved_delta"] == 1
    assert report["cumulative_summary"]["selected_pair_count"] == 8
    assert report["trigger_policy_review"]["continuation_policy"][
        "require_live_trigger_recalibration_protocol_before_batch3"
    ] is True


def test_write_closed_loop_evidence_artifacts(tmp_path: Path) -> None:
    result = write_closed_loop_evidence(
        candidate_rows=_candidate_rows(),
        pair_summary=_pair_summary_rows(),
        output_dir=tmp_path,
        input_artifacts=[
            MODELS / "phase311_trajectory_diagnosis_candidates.jsonl",
            MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl",
        ],
    )

    assert count_jsonl(result["pairs_path"]) == 32
    assert count_jsonl(result["arms_path"]) == 64
    report = json.loads(Path(result["report_path"]).read_text())
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert report["passed"] is True
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 6
    assert all(item["sha256"] for item in manifest["artifacts"])
