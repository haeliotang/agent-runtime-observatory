from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2 import protocol_v2_prescription_template
from wutai_clinic.intervention.protocol_v2_planned_preflight import (
    write_protocol_v2_planned_preflight_evidence,
)
from wutai_clinic.io import count_jsonl

runner = CliRunner()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _candidate(tmp_path: Path) -> dict:
    replay_path = tmp_path / "sympy__fresh_replay_actions.json"
    config_path = tmp_path / "sympy__fresh_run_single_config.json"
    _write_json(replay_path, ["python reproduce.py"])
    _write_json(
        config_path,
        {
            "agent": {
                "model": {
                    "name": "openai/gpt-5.5",
                    "api_key": "sk-test-secret",
                    "api_base": None,
                }
            },
            "output_dir": "/tmp/native-output",
        },
    )
    return {
        "fresh_rank": 1,
        "pair_id": "pair-fresh",
        "source_task_id": "sympy__fresh",
        "source_family": "sympy",
        "selection_role": "failure_target",
        "intervention_policy_id": "break_recurrence_and_replan",
        "replay_risk_level": "materialized_pair_inputs_no_known_replay_mismatch",
        "same_pair_posthoc_positive_claim_allowed": False,
        "phase6_live_pair_authorized": False,
        "official_eval_authorized": False,
        "replay_actions_path": replay_path.as_posix(),
        "run_single_config_path": config_path.as_posix(),
    }


def _candidate_report() -> dict:
    return {
        "decision": "protocol_v2_fresh_candidate_set_ready_limited_underpowered_no_batch_claim",
        "passed": True,
    }


def test_protocol_v2_planned_preflight_generates_runtime_configs_and_mapping(
    tmp_path: Path,
) -> None:
    result = write_protocol_v2_planned_preflight_evidence(
        candidate_set_report=_candidate_report(),
        candidate_rows=[_candidate(tmp_path)],
        protocol_v2=protocol_v2_prescription_template(),
        output_dir=tmp_path / "preflight",
        api_base="https://proxy.example.test/v1",
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_planned_preflight_ready_live_execution_not_authorized"
    assert report["summary"]["replay_action_count"] == 1
    assert report["summary"]["all_steps_mapped"] is True
    assert report["continuation_policy"]["allow_protocol_v2_live_single_execute"] is True
    assert report["continuation_policy"]["allow_protocol_v2_real_run_without_explicit_ack"] is False
    assert count_jsonl(result["mapping_path"]) == 4
    control_config = json.loads(result["control_config_path"].read_text())
    treatment_config = json.loads(result["treatment_config_path"].read_text())
    assert control_config["agent"]["model"]["api_key"] is None
    assert treatment_config["agent"]["model"]["api_base"] == "https://proxy.example.test/v1"
    assert control_config["wutai_clinic"]["arm_type"] == "control"
    assert treatment_config["wutai_clinic"]["arm_type"] == "treatment"
    assert "sk-test-secret" not in result["control_config_path"].read_text()
    assert "sk-test-secret" not in result["treatment_config_path"].read_text()


def test_protocol_v2_planned_preflight_blocks_missing_replay(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    Path(candidate["replay_actions_path"]).unlink()

    result = write_protocol_v2_planned_preflight_evidence(
        candidate_set_report=_candidate_report(),
        candidate_rows=[candidate],
        protocol_v2=protocol_v2_prescription_template(),
        output_dir=tmp_path / "preflight",
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "protocol_v2_planned_preflight_blocked"
    assert "replay_actions_present" in result["report"]["blocking_failures"]


def test_cli_protocol_v2_planned_preflight_writes_package(tmp_path: Path) -> None:
    candidate_report = tmp_path / "candidate_report.json"
    candidate_rows = tmp_path / "candidates.jsonl"
    template = tmp_path / "protocol_v2_template.json"
    output_dir = tmp_path / "preflight"
    _write_json(candidate_report, _candidate_report())
    _write_jsonl(candidate_rows, [_candidate(tmp_path)])
    _write_json(
        template,
        {
            "decision": "protocol_v2_prescription_template_ready_not_live_executed",
            "protocol_v2": protocol_v2_prescription_template().to_dict(),
        },
    )

    result = runner.invoke(
        app,
        [
            "protocol-v2-planned-preflight",
            str(candidate_report),
            str(candidate_rows),
            str(template),
            "-o",
            str(output_dir),
            "--api-base",
            "https://proxy.example.test/v1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["source_task_id"] == "sympy__fresh"
    assert payload["allow_protocol_v2_live_single_execute"] is True
    assert payload["allow_protocol_v2_real_run_without_explicit_ack"] is False
    assert (output_dir / "protocol_v2_planned_preflight_report.json").exists()
