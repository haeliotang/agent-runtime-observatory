from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.workflow_doctor import diagnose_workflow

from conftest import requires_monorepo

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


@requires_monorepo
def test_workflow_doctor_scans_models() -> None:
    report = diagnose_workflow(MODELS)
    assert report.artifact_count > 0
    assert report.planning_artifacts > 0
    assert report.experiment_artifacts > 0
    assert report.decision


def test_cli_help_and_doctor_json() -> None:
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "wutai-clinic" in help_result.stdout
    result = runner.invoke(app, ["doctor", str(MODELS), "--json"])
    assert result.exit_code == 0
    assert "planning_artifacts" in result.stdout
