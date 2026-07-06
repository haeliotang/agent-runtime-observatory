from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.stability import (
    batch_stability_report,
    write_batch_stability_evidence,
)
from wutai_clinic.io import count_jsonl, read_jsonl

from conftest import requires_monorepo

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
runner = CliRunner()


def _batch_rows() -> list[dict]:
    return [
        *read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
        *read_jsonl(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
    ]


def test_batch_stability_report_flags_limited_main_attribution_sample() -> None:
    report = batch_stability_report(_batch_rows())
    summary = report["stability_summary"]
    policy = report["continuation_policy"]

    assert report["passed"] is True
    assert report["decision"] == "batch_stability_probe_needs_more_main_pairs"
    assert summary["total_pair_count"] == 8
    assert summary["main_treatment_pair_count"] == 2
    assert summary["positive_main_count"] == 1
    assert summary["neutral_main_count"] == 1
    assert summary["negative_main_count"] == 0
    assert summary["target_main_pairs_met"] is False
    assert policy["allow_next_small_batch"] is True
    assert policy["allow_stability_claim"] is False
    assert policy["allow_intervention_effect_claim"] is False
    assert policy["allow_efe_str_predictive_claim"] is False


def test_write_batch_stability_evidence_artifacts(tmp_path: Path) -> None:
    result = write_batch_stability_evidence(
        pair_summary=_batch_rows(),
        output_dir=tmp_path,
        input_artifacts=[
            MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl",
            MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl",
        ],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    assert report["decision"] == "batch_stability_probe_needs_more_main_pairs"
    assert count_jsonl(result["pairs_path"]) == 8
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 5
    assert all(item["sha256"] for item in manifest["artifacts"])


@requires_monorepo
def test_cli_batch_stability_writes_probe_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "stability"
    result = runner.invoke(
        app,
        [
            "batch-stability",
            str(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
            str(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "batch_stability_probe_needs_more_main_pairs"
    assert payload["total_pair_count"] == 8
    assert payload["main_treatment_pair_count"] == 2
    assert (output_dir / "batch_stability_report.json").exists()
    assert (output_dir / "batch_stability_summary.json").exists()
