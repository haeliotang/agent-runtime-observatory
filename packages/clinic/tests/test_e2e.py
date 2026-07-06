from __future__ import annotations

import json
from itertools import islice
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.io import count_jsonl, read_jsonl, write_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
runner = CliRunner()


def test_cli_pipeline_prune_analyze_diagnose_scorecard(tmp_path: Path) -> None:
    source = tmp_path / "first10.jsonl"
    pruned = tmp_path / "pruned.jsonl"
    analysis = tmp_path / "analysis.json"
    diagnosis = tmp_path / "diagnosis.jsonl"
    scorecard = tmp_path / "scorecard.json"
    write_jsonl(source, islice(read_jsonl(MODELS / "trajectories_purified.jsonl"), 10))

    prune_result = runner.invoke(
        app,
        ["prune", str(source), "--no-dedup", "--no-target-hygiene", "-o", str(pruned)],
    )
    assert prune_result.exit_code == 0, prune_result.output
    assert count_jsonl(pruned) == 10

    analyze_result = runner.invoke(app, ["analyze", str(pruned), "-o", str(analysis)])
    assert analyze_result.exit_code == 0, analyze_result.output
    analysis_payload = json.loads(analysis.read_text())
    assert analysis_payload["total_trajectories"] == 10
    assert "soft_topological_return" in analysis_payload["metrics"]

    diagnose_result = runner.invoke(app, ["diagnose", str(pruned), "-o", str(diagnosis)])
    assert diagnose_result.exit_code == 0, diagnose_result.output
    assert count_jsonl(diagnosis) == 10

    scorecard_result = runner.invoke(
        app,
        [
            "scorecard",
            str(MODELS / "phase3a_controlled_regression_gate_report.json"),
            "-o",
            str(scorecard),
        ],
    )
    assert scorecard_result.exit_code == 0, scorecard_result.output
    scorecard_payload = json.loads(scorecard.read_text())
    assert scorecard_payload["passed"] is True
    assert {"native", "controlled", "passed"}.issubset(scorecard_payload)
