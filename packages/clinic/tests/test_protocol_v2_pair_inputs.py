from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2_pair_inputs import (
    write_protocol_v2_pair_inputs_evidence,
)

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


def _trajectory_root(tmp_path: Path) -> Path:
    root = tmp_path / "trajectories"
    config = {
        "agent": {"model": {"name": "placeholder", "api_key": "env", "temperature": 1}},
        "env": {"deployment": {"python_standalone_dir": "/tmp/python"}},
    }
    _write_json(
        root / "run" / "sympy__fresh" / "sympy__fresh.traj",
        {
            "environment": "sympy__fresh",
            "replay_config": json.dumps(config),
            "trajectory": [
                {
                    "thought": "Inspect file.",
                    "action": (
                        "str_replace_editor insert /testbed/pkg.py --file_text '' "
                        "--view_range  --old_str '' --new_str 'x = 1' --insert_line 1"
                    ),
                },
                {"thought": "Run check.", "action": "python -m pytest tests/test_pkg.py"},
            ],
        },
    )
    return root


def _candidate() -> dict:
    return {
        "pair_id": "pair-fresh",
        "source_task_id": "sympy__fresh",
        "source_family": "sympy",
        "selection_role": "failure_target",
        "intervention_policy_id": "same_action_escape",
        "candidate_prefix_index": 2,
        "candidate_reason_codes": ["same_action_family_streak"],
    }


def test_protocol_v2_pair_inputs_materializes_secret_free_replay(tmp_path: Path) -> None:
    result = write_protocol_v2_pair_inputs_evidence(
        candidate_rows=[_candidate()],
        trajectory_root=_trajectory_root(tmp_path),
        output_root=tmp_path / "inputs",
        native_root=tmp_path / "native",
        pair_ids=["pair-fresh"],
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_pair_inputs_batch_ready"
    assert report["summary"]["materialized_count"] == 1
    child_report = result["results"][0]["report"]
    assert child_report["decision"] == "protocol_v2_pair_inputs_ready"
    assert (
        child_report["replay_determinism_screen"]["risk_level"] == "low_replay_nondeterminism_risk"
    )
    assert child_report["gates"]["runner_not_started"] is True
    replay_path = result["results"][0]["replay_path"]
    replay = json.loads(replay_path.read_text())
    arguments = json.loads(replay[0]["tool_calls"][0]["function"]["arguments"])
    assert "view_range" not in arguments


def test_cli_protocol_v2_pair_inputs_writes_batch_report(tmp_path: Path) -> None:
    candidate_source = tmp_path / "candidates.jsonl"
    _write_jsonl(candidate_source, [_candidate()])

    result = runner.invoke(
        app,
        [
            "protocol-v2-materialize-pair-inputs",
            str(candidate_source),
            "--trajectory-root",
            str(_trajectory_root(tmp_path)),
            "--output-root",
            str(tmp_path / "inputs"),
            "--native-root",
            str(tmp_path / "native"),
            "--pair-id",
            "pair-fresh",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["materialized_count"] == 1
    assert (tmp_path / "inputs" / "protocol_v2_pair_inputs_batch_report.json").exists()
