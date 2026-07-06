from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2 import protocol_v2_prescription_template
from wutai_clinic.intervention.protocol_v2_dry_run import (
    PASS_DECISION,
    protocol_v2_dry_run_report,
    write_protocol_v2_dry_run_evidence,
)
from wutai_clinic.io import count_jsonl

runner = CliRunner()


def _report(decision: str = "protocol_v1_batch_outcomes_underpowered_no_uplift_observed") -> dict:
    return {
        "decision": decision,
        "passed": True,
        "summary": {
            "protocol_v1_pair_count": 1,
            "protocol_v1_no_uplift_count": 1,
            "protocol_v1_trajectory_outcome_counts": {"trajectory_diverged_no_uplift": 1},
        },
    }


def _rows(*, target: bool = True) -> list[dict]:
    trajectory_class = (
        "trajectory_diverged_no_uplift" if target else "hook_no_behavior_shift_no_uplift"
    )
    return [
        {
            "protocol_family": "protocol_v1_constraint_hook",
            "pair_id": "phase312_pair_018_failure_target_error_observation_recovery",
            "source_task_id": "matplotlib__matplotlib-25079",
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
            "trajectory_outcome_class": trajectory_class,
            "outcome_source": "official_eval",
        }
    ]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_protocol_v2_dry_run_builds_prospective_plan_from_no_uplift_dynamics() -> None:
    report = protocol_v2_dry_run_report(
        batch_outcomes_report=_report(),
        outcome_rows=_rows(),
        protocol_v2=protocol_v2_prescription_template(),
    )

    assert report["passed"] is True
    assert report["decision"] == PASS_DECISION
    assert report["summary"]["target_pair_count"] == 1
    assert report["summary"]["runner_started"] is False
    assert report["summary"]["model_call_started"] is False
    assert report["continuation_policy"]["allow_protocol_v2_live_single_planned_preflight"] is True
    assert report["continuation_policy"]["allow_protocol_v2_real_run"] is False
    assert report["continuation_policy"]["allow_same_pair_positive_claim"] is False


def test_protocol_v2_dry_run_blocks_without_trajectory_diverged_target() -> None:
    report = protocol_v2_dry_run_report(
        batch_outcomes_report=_report(),
        outcome_rows=_rows(target=False),
        protocol_v2=protocol_v2_prescription_template(),
    )

    assert report["passed"] is False
    assert report["decision"] == "protocol_v2_dry_run_gate_blocked"
    assert report["gates"]["target_rows_present"] is False
    assert report["continuation_policy"]["allow_protocol_v2_live_single_planned_preflight"] is False


def test_write_protocol_v2_dry_run_evidence_artifacts(tmp_path: Path) -> None:
    source_report = tmp_path / "batch_outcomes_report.json"
    rows_path = tmp_path / "batch_outcomes_pairs.jsonl"
    _write_json(source_report, _report())
    _write_jsonl(rows_path, _rows())

    result = write_protocol_v2_dry_run_evidence(
        batch_outcomes_report=json.loads(source_report.read_text()),
        outcome_rows=_rows(),
        protocol_v2=protocol_v2_prescription_template(),
        output_dir=tmp_path / "dry-run",
        input_artifacts=[source_report, rows_path],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    assert report["decision"] == PASS_DECISION
    assert count_jsonl(result["rows_path"]) == 1
    assert count_jsonl(result["events_path"]) == 1
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 7
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_cli_protocol_v2_dry_run_writes_evidence_package(tmp_path: Path) -> None:
    source_report = tmp_path / "batch_outcomes_report.json"
    rows_path = tmp_path / "batch_outcomes_pairs.jsonl"
    template_path = tmp_path / "protocol_v2_template.json"
    output_dir = tmp_path / "cli-dry-run"
    _write_json(source_report, _report())
    _write_jsonl(rows_path, _rows())
    _write_json(
        template_path,
        {
            "decision": "protocol_v2_prescription_template_ready_not_live_executed",
            "protocol_v2": protocol_v2_prescription_template().to_dict(),
        },
    )

    result = runner.invoke(
        app,
        [
            "protocol-v2-dry-run",
            str(source_report),
            str(rows_path),
            "-o",
            str(output_dir),
            "--protocol-v2-template",
            str(template_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == PASS_DECISION
    assert payload["target_pair_count"] == 1
    assert payload["allow_protocol_v2_live_single_planned_preflight"] is True
    assert payload["allow_protocol_v2_real_run"] is False
    assert (output_dir / "protocol_v2_dry_run_report.json").exists()
