from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2_fresh_candidates import (
    write_protocol_v2_fresh_candidate_evidence,
)
from wutai_clinic.io import count_jsonl, read_jsonl

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


def _candidate(pair_id: str, task_id: str, *, role: str = "failure_target") -> dict:
    return {
        "pair_id": pair_id,
        "source_task_id": task_id,
        "source_family": task_id.split("__", 1)[0],
        "selection_role": role,
        "selection_status": "eligible_for_live_pair",
        "intervention_policy_id": "break_recurrence_and_replan",
        "candidate_static_prefix_index": 7,
    }


def _materialize_inputs(root: Path, task_id: str) -> None:
    task_dir = root / task_id
    _write_jsonl(task_dir / f"{task_id}_candidate.jsonl", [_candidate(f"pair-{task_id}", task_id)])
    _write_json(task_dir / f"{task_id}_replay_actions.json", ["python reproduce.py"])
    _write_json(
        task_dir / f"{task_id}_run_single_config.json", {"agent": {"model": {"api_key": None}}}
    )
    _write_json(
        task_dir / f"{task_id}_live_pair_inputs_report.json",
        {
            "decision": "phase6_state_capsule_pair_inputs_ready",
            "passed": True,
            "gates": {"replay_actions_written": True},
            "replay_determinism_screen": {
                "risk_level": "no_known_replay_nondeterminism_patterns",
            },
        },
    )


def test_protocol_v2_fresh_candidates_keep_only_uncontaminated_failure_target(
    tmp_path: Path,
) -> None:
    official_root = tmp_path / "official"
    input_root = tmp_path / "pair-inputs"
    _materialize_inputs(input_root, "sympy__fresh")
    _materialize_inputs(input_root, "django__used")
    _materialize_inputs(input_root, "matplotlib__mismatch")
    _write_json(
        official_root / "used" / "phase6_dual_scorecard.json",
        {
            "pair_id": "pair-used",
            "source_task_id": "django__used",
            "official_eval_completed": True,
        },
    )
    _write_json(
        official_root / "mismatch" / "phase6_state_capsule_mismatch_audit_report.json",
        {
            "pair_id": "pair-mismatch",
            "source_task_id": "matplotlib__mismatch",
            "decision": "phase6_state_capsule_mismatch_audit_ready_replay_prefix_nondeterministic",
            "likely_root_cause": "nondeterministic_git_stash_output_changed_message_prefix",
        },
    )

    result = write_protocol_v2_fresh_candidate_evidence(
        candidate_rows=[
            _candidate("pair-fresh", "sympy__fresh"),
            _candidate("pair-used", "django__used"),
            _candidate("pair-mismatch", "matplotlib__mismatch"),
            _candidate("pair-sentinel", "sympy__sentinel", role="success_sentinel"),
        ],
        protocol_v2_dry_run_report={
            "decision": "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
        },
        official_eval_roots=[official_root],
        pair_input_roots=[input_root],
        output_dir=tmp_path / "out",
        target_pair_count=4,
    )

    report = result["report"]
    assert report["passed"] is True
    assert (
        report["decision"]
        == "protocol_v2_fresh_candidate_set_ready_limited_underpowered_no_batch_claim"
    )
    assert report["summary"]["fresh_candidate_count"] == 1
    assert report["summary"]["exclusion_counts"] == {
        "known_replay_nondeterminism_or_state_mismatch": 1,
        "not_failure_target": 1,
        "official_eval_completed_contaminated": 1,
    }
    rows = list(read_jsonl(result["fresh_path"]))
    assert rows[0]["source_task_id"] == "sympy__fresh"
    assert rows[0]["protocol_v2_required"] is True
    assert "forbidden_trigger_inputs" not in rows[0]
    assert count_jsonl(result["excluded_path"]) == 3


def test_cli_protocol_v2_fresh_candidates_writes_package(tmp_path: Path) -> None:
    candidate_source = tmp_path / "candidates.jsonl"
    dry_run_report = tmp_path / "dry-run-report.json"
    official_root = tmp_path / "official"
    input_root = tmp_path / "pair-inputs"
    output_dir = tmp_path / "out"
    _materialize_inputs(input_root, "sympy__fresh")
    _write_jsonl(candidate_source, [_candidate("pair-fresh", "sympy__fresh")])
    _write_json(
        dry_run_report,
        {"decision": "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"},
    )

    result = runner.invoke(
        app,
        [
            "protocol-v2-fresh-candidates",
            str(candidate_source),
            "--protocol-v2-dry-run-report",
            str(dry_run_report),
            "--official-eval-root",
            str(official_root),
            "--pair-input-root",
            str(input_root),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["fresh_candidate_count"] == 1
    assert payload["allow_protocol_v2_planned_preflight"] is True
    assert payload["allow_protocol_v2_real_run"] is False
    assert (output_dir / "protocol_v2_fresh_candidate_set_report.json").exists()


def test_protocol_v2_fresh_candidates_exclude_historical_official_eval_jsonl(
    tmp_path: Path,
) -> None:
    official_root = tmp_path / "official"
    input_root = tmp_path / "pair-inputs"
    _materialize_inputs(input_root, "sympy__used")
    _write_jsonl(
        official_root / "phase316_cumulative_pair_diagnosis.jsonl",
        [
            {
                "pair_id": "pair-used",
                "source_task_id": "sympy__used",
                "official_eval_completed": True,
            }
        ],
    )

    result = write_protocol_v2_fresh_candidate_evidence(
        candidate_rows=[_candidate("pair-used", "sympy__used")],
        protocol_v2_dry_run_report={
            "decision": "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
        },
        official_eval_roots=[official_root],
        pair_input_roots=[input_root],
        output_dir=tmp_path / "out",
        target_pair_count=4,
    )

    report = result["report"]
    assert report["passed"] is False
    assert report["decision"] == "protocol_v2_fresh_candidate_set_blocked_no_fresh_failure_targets"
    assert report["summary"]["exclusion_counts"] == {
        "official_eval_completed_contaminated": 1,
    }


def test_protocol_v2_fresh_candidates_coalesce_duplicate_to_complete_row(
    tmp_path: Path,
) -> None:
    official_root = tmp_path / "official"
    input_root = tmp_path / "pair-inputs"
    _materialize_inputs(input_root, "sympy__fresh")
    shallow = {
        "pair_id": "pair-fresh",
        "source_task_id": "sympy__fresh",
        "selection_role": "failure_target",
    }
    complete = _candidate("pair-fresh", "sympy__fresh")
    complete["candidate_reason_codes"] = ["same_action_family_streak"]

    result = write_protocol_v2_fresh_candidate_evidence(
        candidate_rows=[shallow, complete],
        protocol_v2_dry_run_report={
            "decision": "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
        },
        official_eval_roots=[official_root],
        pair_input_roots=[input_root],
        output_dir=tmp_path / "out",
        target_pair_count=1,
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_fresh_candidate_set_ready_for_planned_preflight"
    assert report["summary"]["exclusion_counts"] == {"duplicate_candidate": 1}
    rows = list(read_jsonl(result["fresh_path"]))
    assert rows[0]["intervention_policy_id"] == "break_recurrence_and_replan"
    assert rows[0]["candidate_reason_codes"] == ["same_action_family_streak"]


def test_protocol_v2_fresh_candidates_exclude_high_replay_risk(
    tmp_path: Path,
) -> None:
    official_root = tmp_path / "official"
    input_root = tmp_path / "pair-inputs"
    _materialize_inputs(input_root, "sympy__risky")
    report_path = input_root / "sympy__risky" / "sympy__risky_live_pair_inputs_report.json"
    report = json.loads(report_path.read_text())
    report["replay_determinism_screen"]["risk_level"] = "high_replay_nondeterminism_risk"
    _write_json(report_path, report)

    result = write_protocol_v2_fresh_candidate_evidence(
        candidate_rows=[_candidate("pair-risky", "sympy__risky")],
        protocol_v2_dry_run_report={
            "decision": "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
        },
        official_eval_roots=[official_root],
        pair_input_roots=[input_root],
        output_dir=tmp_path / "out",
        target_pair_count=4,
    )

    report = result["report"]
    assert report["passed"] is False
    assert report["summary"]["exclusion_counts"] == {"replay_risk_not_allowed": 1}
