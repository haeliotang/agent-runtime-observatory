from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.instance_validity import (
    CLAIM_BOUNDARY,
    classify_instance_validity,
    find_gold_sanity_reports,
    write_instance_validity_evidence,
)

runner = CliRunner()


def _write_sanity_report(root: Path, instance_id: str, *, resolved: bool) -> Path:
    path = (
        root
        / "protocol_v2_oracle_probe"
        / instance_id
        / "gold_sanity"
        / "logs"
        / "run_evaluation"
        / "gold_sanity"
        / f"gold_sanity__{instance_id}"
        / instance_id
        / "report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                instance_id: {
                    "resolved": resolved,
                    "tests_status": {
                        "FAIL_TO_PASS": {
                            "success": ["t1"] if resolved else [],
                            "failure": [] if resolved else ["t1", "t2"],
                        },
                        "PASS_TO_PASS": {"success": [], "failure": []},
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_find_gold_sanity_reports(tmp_path: Path) -> None:
    _write_sanity_report(tmp_path, "a__b-1", resolved=True)
    _write_sanity_report(tmp_path, "c__d-2", resolved=False)
    reports = find_gold_sanity_reports(tmp_path)
    assert set(reports) == {"a__b-1", "c__d-2"}


def test_classify_valid_and_invalid(tmp_path: Path) -> None:
    valid_path = _write_sanity_report(tmp_path, "a__b-1", resolved=True)
    invalid_path = _write_sanity_report(tmp_path, "c__d-2", resolved=False)
    valid = classify_instance_validity(valid_path, "a__b-1")
    invalid = classify_instance_validity(invalid_path, "c__d-2")
    assert valid["substrate_valid"] is True
    assert invalid["substrate_valid"] is False
    assert invalid["fail_to_pass_passed"] == 0
    assert invalid["fail_to_pass_total"] == 2


def test_classify_missing_instance_returns_none(tmp_path: Path) -> None:
    path = _write_sanity_report(tmp_path, "a__b-1", resolved=True)
    assert classify_instance_validity(path, "other__x-9") is None


def test_write_evidence_invalid_found(tmp_path: Path) -> None:
    _write_sanity_report(tmp_path, "a__b-1", resolved=True)
    _write_sanity_report(tmp_path, "c__d-2", resolved=False)
    result = write_instance_validity_evidence(tmp_path, tmp_path / "out")
    report = result["report"]
    assert report["decision"] == "instance_validity_substrate_invalid_instances_found"
    assert report["valid_instances"] == ["a__b-1"]
    assert report["invalid_instances"] == ["c__d-2"]
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert "void" in report["voiding_rule"]


def test_write_evidence_all_valid(tmp_path: Path) -> None:
    _write_sanity_report(tmp_path, "a__b-1", resolved=True)
    result = write_instance_validity_evidence(tmp_path, tmp_path / "out")
    assert result["report"]["decision"] == "instance_validity_all_checked_instances_valid"
    assert result["report"]["passed"] is True


def test_write_evidence_blocked_no_reports(tmp_path: Path) -> None:
    result = write_instance_validity_evidence(tmp_path, tmp_path / "out")
    assert result["report"]["decision"] == "instance_validity_blocked_no_gold_sanity_reports"
    assert result["report"]["passed"] is False


def test_cli_instance_validity(tmp_path: Path) -> None:
    _write_sanity_report(tmp_path, "a__b-1", resolved=False)
    out = tmp_path / "out"
    result = runner.invoke(app, ["instance-validity", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["invalid_instances"] == ["a__b-1"]
    assert (out / "instance_validity_report.json").is_file()
