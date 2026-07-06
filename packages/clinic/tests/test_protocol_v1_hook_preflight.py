from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1 import protocol_v1_for_no_uplift_classification
from wutai_clinic.intervention.protocol_v1_hook_preflight import (
    protocol_v1_hook_preflight_report,
    write_protocol_v1_hook_preflight_evidence,
)
from wutai_clinic.io import count_jsonl

runner = CliRunner()


def _plan() -> dict:
    target_protocol = protocol_v1_for_no_uplift_classification(
        classification="behavior_diverged_but_target_failure_persisted",
        trigger_predicate="error_streak >= 1",
    )
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
                "protocol_v1": target_protocol.to_dict(),
                "protocol_hash": target_protocol.protocol_hash,
                "runtime_oracle_source": "prefix_observation_required",
                "same_pair_rerun_attribution_eligible": False,
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


def _adapter_preflight_report() -> dict:
    return {
        "decision": "protocol_v1_sweagent_adapter_preflight_ready_no_run",
        "passed": True,
        "continuation_policy": {
            "allow_protocol_v1_constraint_hook_implementation": True,
            "allow_protocol_v1_real_run": False,
        },
    }


def test_protocol_v1_hook_preflight_proves_actual_blocking_without_live_run() -> None:
    report = protocol_v1_hook_preflight_report(
        plan=_plan(),
        adapter_preflight_report=_adapter_preflight_report(),
    )

    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_constraint_hook_preflight_passed_no_live_run"
    assert report["summary"]["blocking_event_count"] == 5
    assert report["summary"]["blocked_constraint_counts"] == {
        "block_edit_until_failure_reproduced_or_explained": 1,
        "block_submit_on_guard_regression": 1,
        "require_post_patch_guard_recheck": 1,
        "require_post_patch_target_recheck": 2,
    }
    assert report["summary"]["runner_started"] is False
    assert report["summary"]["model_call_started"] is False
    assert report["summary"]["docker_or_official_eval_started"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_single_adapter_integration"] is True
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False


def test_protocol_v1_hook_preflight_blocks_when_adapter_preflight_not_ready() -> None:
    adapter_report = _adapter_preflight_report()
    adapter_report["passed"] = False

    report = protocol_v1_hook_preflight_report(
        plan=_plan(),
        adapter_preflight_report=adapter_report,
    )

    assert report["passed"] is False
    assert report["decision"] == "protocol_v1_constraint_hook_preflight_blocked"
    assert report["gates"]["adapter_preflight_passed"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_single_adapter_integration"] is False
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False


def test_write_protocol_v1_hook_preflight_evidence_artifacts(tmp_path: Path) -> None:
    plan_path = tmp_path / "protocol_v1_plan.json"
    adapter_path = tmp_path / "adapter_preflight_report.json"
    plan_path.write_text(json.dumps(_plan(), indent=2, sort_keys=True) + "\n")
    adapter_path.write_text(json.dumps(_adapter_preflight_report(), indent=2) + "\n")

    result = write_protocol_v1_hook_preflight_evidence(
        protocol_v1_plan=json.loads(plan_path.read_text()),
        adapter_preflight_report=json.loads(adapter_path.read_text()),
        output_dir=tmp_path / "hook-preflight",
        input_artifacts=[plan_path, adapter_path],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    assert report["passed"] is True
    assert count_jsonl(result["events_path"]) == report["summary"]["hook_event_count"]
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 5
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_cli_protocol_v1_hook_preflight_writes_evidence_package(tmp_path: Path) -> None:
    plan_path = tmp_path / "protocol_v1_plan.json"
    adapter_path = tmp_path / "adapter_preflight_report.json"
    plan_path.write_text(json.dumps(_plan(), indent=2, sort_keys=True) + "\n")
    adapter_path.write_text(json.dumps(_adapter_preflight_report(), indent=2) + "\n")
    output_dir = tmp_path / "cli-hook-preflight"

    result = runner.invoke(
        app,
        [
            "protocol-v1-hook-preflight",
            str(plan_path),
            str(adapter_path),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "protocol_v1_constraint_hook_preflight_passed_no_live_run"
    assert payload["allow_protocol_v1_live_single_adapter_integration"] is True
    assert payload["allow_protocol_v1_real_run"] is False
    assert (output_dir / "protocol_v1_constraint_hook_preflight_report.json").exists()
    assert (output_dir / "protocol_v1_constraint_hook_preflight_events.jsonl").exists()
