from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.batch_readiness import (
    batch3_readiness_report,
    write_batch3_readiness_evidence,
)
from wutai_clinic.intervention.stability import batch_stability_report
from wutai_clinic.io import count_jsonl, read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
runner = CliRunner()


def _batch_rows() -> list[dict]:
    return [
        *read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
        *read_jsonl(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
    ]


def _stability_report() -> dict:
    return batch_stability_report(_batch_rows())


def _trigger_review() -> dict:
    return json.loads((MODELS / "phase316_trigger_policy_review_report.json").read_text())


def _recalibration_report() -> dict:
    return json.loads((MODELS / "phase316_live_trigger_recalibration_report.json").read_text())


def _recalibration_protocol() -> dict:
    return json.loads((MODELS / "phase316_live_trigger_recalibration_protocol.json").read_text())


def _candidate_rows() -> list[dict]:
    return list(read_jsonl(MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl"))


def _dry_run_report() -> dict:
    return json.loads((MODELS / "phase316_live_feature_hook_dry_run_report.json").read_text())


def test_batch3_readiness_allows_preflight_but_not_real_run() -> None:
    report = batch3_readiness_report(
        stability_report=_stability_report(),
        trigger_policy_review=_trigger_review(),
        recalibration_report=_recalibration_report(),
        recalibration_protocol=_recalibration_protocol(),
        candidate_rows=_candidate_rows(),
        live_feature_dry_run_report=_dry_run_report(),
    )

    assert report["passed"] is True
    assert (
        report["decision"]
        == "batch3_readiness_live_feature_dry_run_ready_external_run_not_authorized"
    )
    assert report["gates"]["same_static_prefix_policy_blocked"] is True
    assert report["gates"]["dry_run_injected_once_per_candidate"] is True
    assert report["readiness_summary"]["candidate_count"] == 4
    assert report["continuation_policy"]["allow_live_hook_runner_preflight"] is True
    assert report["continuation_policy"]["allow_batch3_static_prefix_run"] is False
    assert report["continuation_policy"]["allow_batch3_real_run"] is False
    assert report["continuation_policy"]["allow_full_64_unattended"] is False


def test_batch3_readiness_without_dry_run_requires_dry_run_first() -> None:
    report = batch3_readiness_report(
        stability_report=_stability_report(),
        trigger_policy_review=_trigger_review(),
        recalibration_report=_recalibration_report(),
        recalibration_protocol=_recalibration_protocol(),
        candidate_rows=_candidate_rows(),
    )

    assert report["passed"] is True
    assert report["decision"] == "batch3_readiness_recalibration_ready_live_feature_dry_run_required"
    assert report["readiness_summary"]["dry_run_present"] is False
    assert report["continuation_policy"]["allow_prepare_batch3_candidate_review"] is True
    assert report["continuation_policy"]["allow_live_hook_runner_preflight"] is False
    assert report["continuation_policy"]["allow_batch3_real_run"] is False


def test_batch3_readiness_blocks_if_static_prefix_policy_allowed() -> None:
    trigger_review = deepcopy(_trigger_review())
    trigger_review["continuation_policy"]["allow_batch3_same_static_prefix_policy"] = True

    report = batch3_readiness_report(
        stability_report=_stability_report(),
        trigger_policy_review=trigger_review,
        recalibration_report=_recalibration_report(),
        recalibration_protocol=_recalibration_protocol(),
        candidate_rows=_candidate_rows(),
        live_feature_dry_run_report=_dry_run_report(),
    )

    assert report["passed"] is False
    assert report["decision"] == "batch3_readiness_blocked"
    assert report["gates"]["same_static_prefix_policy_blocked"] is False
    assert report["continuation_policy"]["allow_live_hook_runner_preflight"] is False
    assert report["continuation_policy"]["allow_batch3_real_run"] is False


def test_write_batch3_readiness_evidence_artifacts(tmp_path: Path) -> None:
    result = write_batch3_readiness_evidence(
        stability_report=_stability_report(),
        trigger_policy_review=_trigger_review(),
        recalibration_report=_recalibration_report(),
        recalibration_protocol=_recalibration_protocol(),
        candidate_rows=_candidate_rows(),
        live_feature_dry_run_report=_dry_run_report(),
        output_dir=tmp_path,
        input_artifacts=[
            MODELS / "phase316_trigger_policy_review_report.json",
            MODELS / "phase316_live_trigger_recalibration_report.json",
            MODELS / "phase316_live_trigger_recalibration_protocol.json",
            MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl",
            MODELS / "phase316_live_feature_hook_dry_run_report.json",
        ],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    assert (
        report["decision"]
        == "batch3_readiness_live_feature_dry_run_ready_external_run_not_authorized"
    )
    assert count_jsonl(result["candidates_path"]) == 4
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 8
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_cli_batch3_readiness_writes_evidence_package(tmp_path: Path) -> None:
    stability = tmp_path / "batch_stability_report.json"
    stability.write_text(json.dumps(_stability_report(), indent=2, sort_keys=True) + "\n")
    output_dir = tmp_path / "batch3-readiness"

    result = runner.invoke(
        app,
        [
            "batch3-readiness",
            str(stability),
            str(MODELS / "phase316_trigger_policy_review_report.json"),
            str(MODELS / "phase316_live_trigger_recalibration_report.json"),
            str(MODELS / "phase316_live_trigger_recalibration_protocol.json"),
            str(MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl"),
            "--live-feature-dry-run-report",
            str(MODELS / "phase316_live_feature_hook_dry_run_report.json"),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert (
        payload["decision"]
        == "batch3_readiness_live_feature_dry_run_ready_external_run_not_authorized"
    )
    assert payload["candidate_count"] == 4
    assert payload["dry_run_present"] is True
    assert payload["allow_live_hook_runner_preflight"] is True
    assert payload["allow_batch3_real_run"] is False
    assert (output_dir / "batch3_readiness_report.json").exists()
    assert (output_dir / "batch3_readiness_summary.json").exists()
