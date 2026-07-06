from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.sweagent_protocol_v1_preflight import (
    sweagent_protocol_v1_preflight_report,
    write_sweagent_protocol_v1_preflight_evidence,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1 import protocol_v1_for_no_uplift_classification
from wutai_clinic.intervention.protocol_v1_dry_run import protocol_v1_dry_run_report
from wutai_clinic.io import count_jsonl

runner = CliRunner()


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "run_single.yaml"
    config.write_text("agent:\n  model:\n    name: fake\n", encoding="utf-8")
    return config


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


def test_sweagent_protocol_v1_preflight_maps_constraints_without_running(
    tmp_path: Path,
) -> None:
    plan = _plan()
    report = sweagent_protocol_v1_preflight_report(
        plan=plan,
        dry_run_report=protocol_v1_dry_run_report(plan),
        run_single_config=_config(tmp_path),
        commands={"pairs": []},
    )

    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_sweagent_adapter_preflight_ready_no_run"
    assert report["summary"]["adapter_event_count"] == 6
    assert report["summary"]["blocking_event_count"] == 2
    assert report["summary"]["runner_started"] is False
    assert report["summary"]["model_call_started"] is False
    assert report["summary"]["docker_or_official_eval_started"] is False
    assert report["continuation_policy"]["allow_protocol_v1_constraint_hook_implementation"] is True
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False
    assert report["gates"]["official_eval_identifiers_not_runtime_visible"] is True


def test_sweagent_protocol_v1_preflight_blocks_when_dry_run_not_passed(tmp_path: Path) -> None:
    plan = _plan()
    dry_run = protocol_v1_dry_run_report(plan)
    dry_run["passed"] = False

    report = sweagent_protocol_v1_preflight_report(
        plan=plan,
        dry_run_report=dry_run,
        run_single_config=_config(tmp_path),
        commands={"pairs": []},
    )

    assert report["passed"] is False
    assert report["decision"] == "protocol_v1_sweagent_adapter_preflight_blocked"
    assert report["gates"]["dry_run_report_passed"] is False
    assert report["continuation_policy"]["allow_protocol_v1_constraint_hook_implementation"] is False
    assert report["continuation_policy"]["allow_protocol_v1_real_run"] is False


def test_write_sweagent_protocol_v1_preflight_evidence_artifacts(tmp_path: Path) -> None:
    plan = _plan()
    plan_path = tmp_path / "protocol_v1_plan.json"
    dry_run_path = tmp_path / "protocol_v1_dry_run_report.json"
    config_path = _config(tmp_path)
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    dry_run_path.write_text(
        json.dumps(protocol_v1_dry_run_report(plan), indent=2, sort_keys=True) + "\n"
    )

    result = write_sweagent_protocol_v1_preflight_evidence(
        protocol_v1_plan=plan,
        dry_run_report=json.loads(dry_run_path.read_text()),
        run_single_config=config_path,
        output_dir=tmp_path / "preflight",
        input_artifacts=[plan_path, dry_run_path, config_path],
    )

    report = json.loads(result["report_path"].read_text())
    manifest = json.loads(result["manifest_path"].read_text())
    commands = json.loads(result["commands_path"].read_text())
    assert report["passed"] is True
    assert count_jsonl(result["events_path"]) == 6
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 9
    assert commands["requires_protocol_v1_constraint_hook_before_execute"] is True
    assert commands["pairs"][0]["planned_command"].startswith(
        "wutai-clinic sweagent-protocol-v1-live-single"
    )
    assert "--protocol" in commands["pairs"][0]["planned_command"]


def test_cli_protocol_v1_sweagent_preflight_writes_package(tmp_path: Path) -> None:
    plan = _plan()
    plan_path = tmp_path / "protocol_v1_plan.json"
    dry_run_path = tmp_path / "protocol_v1_dry_run_report.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    dry_run_path.write_text(
        json.dumps(protocol_v1_dry_run_report(plan), indent=2, sort_keys=True) + "\n"
    )
    output_dir = tmp_path / "cli-preflight"

    result = runner.invoke(
        app,
        [
            "protocol-v1-sweagent-preflight",
            str(plan_path),
            str(dry_run_path),
            str(_config(tmp_path)),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "protocol_v1_sweagent_adapter_preflight_ready_no_run"
    assert payload["allow_protocol_v1_constraint_hook_implementation"] is True
    assert payload["allow_protocol_v1_real_run"] is False
    assert (output_dir / "protocol_v1_sweagent_adapter_preflight_report.json").exists()
    assert (output_dir / "protocol_v1_sweagent_adapter_preflight_events.jsonl").exists()
