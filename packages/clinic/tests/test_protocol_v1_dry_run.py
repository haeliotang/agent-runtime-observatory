from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1 import protocol_v1_for_no_uplift_classification
from wutai_clinic.intervention.protocol_v1_dry_run import (
    protocol_v1_dry_run_report,
    write_protocol_v1_dry_run_evidence,
)
from wutai_clinic.io import count_jsonl

runner = CliRunner()


def _plan(*, runtime_visible: bool = False, same_pair_allowed: bool = False) -> dict:
    target_protocol = protocol_v1_for_no_uplift_classification(
        classification="behavior_diverged_but_target_failure_persisted",
        trigger_predicate="error_streak >= 1",
    ).to_dict()
    target_protocol["guard"]["official_eval_identifiers_runtime_visible"] = runtime_visible
    regression_protocol = protocol_v1_for_no_uplift_classification(
        classification="target_fixed_but_regression_not_controlled",
        trigger_predicate="same_action_family_streak >= 3",
    )
    return {
        "decision": "protocol_v1_plan_ready_not_live_executed",
        "pair_count": 2,
        "same_pair_positive_claim_allowed": False,
        "pairs": [
            {
                "source_task_id": "pytest-dev__pytest-8365",
                "pair_id": "pair-1",
                "protocol_v1": target_protocol,
                "protocol_hash": protocol_v1_for_no_uplift_classification(
                    classification="behavior_diverged_but_target_failure_persisted",
                    trigger_predicate="error_streak >= 1",
                ).protocol_hash,
                "runtime_oracle_source": "prefix_observation_required",
                "same_pair_rerun_attribution_eligible": same_pair_allowed,
                "official_eval_tests_analysis_only": {
                    "target_failures": ["testing/test_tmpdir.py::target"],
                    "target_successes": [],
                    "guard_failures": [],
                },
            },
            {
                "source_task_id": "matplotlib__matplotlib-24970",
                "pair_id": "pair-2",
                "protocol_v1": regression_protocol.to_dict(),
                "protocol_hash": regression_protocol.protocol_hash,
                "runtime_oracle_source": "prefix_observation_required",
                "same_pair_rerun_attribution_eligible": False,
                "official_eval_tests_analysis_only": {
                    "target_failures": [],
                    "target_successes": ["target"],
                    "guard_failures": ["regression"],
                },
            },
        ],
    }


def test_protocol_v1_dry_run_passes_without_live_execution() -> None:
    report = protocol_v1_dry_run_report(_plan())

    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_dry_run_gate_passed_live_execution_not_authorized"
    assert report["summary"]["event_count"] == 2
    assert report["summary"]["runner_started"] is False
    assert report["summary"]["model_call_started"] is False
    assert report["summary"]["docker_or_official_eval_started"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_hook_adapter_preflight"] is True
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False
    assert report["continuation_policy"]["allow_same_pair_positive_claim"] is False


def test_protocol_v1_dry_run_blocks_runtime_visible_official_oracle() -> None:
    report = protocol_v1_dry_run_report(_plan(runtime_visible=True))

    assert report["passed"] is False
    assert report["decision"] == "protocol_v1_dry_run_gate_blocked"
    assert report["gates"]["protocol_rows_valid"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_hook_adapter_preflight"] is False
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False


def test_protocol_v1_dry_run_blocks_same_pair_attribution() -> None:
    report = protocol_v1_dry_run_report(_plan(same_pair_allowed=True))

    assert report["passed"] is False
    assert report["gates"]["same_pair_rerun_attribution_blocked"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_hook_adapter_preflight"] is False


def test_write_protocol_v1_dry_run_evidence_artifacts(tmp_path: Path) -> None:
    plan_path = tmp_path / "protocol_v1_plan.json"
    plan_path.write_text(json.dumps(_plan(), indent=2, sort_keys=True) + "\n")

    result = write_protocol_v1_dry_run_evidence(
        protocol_v1_plan=json.loads(plan_path.read_text()),
        output_dir=tmp_path / "dry-run",
        input_artifacts=[plan_path],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    summary = json.loads(result["summary_path"].read_text())
    assert report["passed"] is True
    assert summary["decision"] == "protocol_v1_dry_run_gate_passed_live_execution_not_authorized"
    assert count_jsonl(result["events_path"]) == 2
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 4
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_cli_protocol_v1_dry_run_writes_evidence_package(tmp_path: Path) -> None:
    plan_path = tmp_path / "protocol_v1_plan.json"
    plan_path.write_text(json.dumps(_plan(), indent=2, sort_keys=True) + "\n")
    output_dir = tmp_path / "cli-dry-run"

    result = runner.invoke(
        app,
        [
            "protocol-v1-dry-run",
            str(plan_path),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["allow_protocol_v1_live_hook_adapter_preflight"] is True
    assert payload["allow_protocol_v1_real_run"] is False
    assert (output_dir / "protocol_v1_dry_run_report.json").exists()
    assert (output_dir / "protocol_v1_dry_run_manifest.json").exists()
