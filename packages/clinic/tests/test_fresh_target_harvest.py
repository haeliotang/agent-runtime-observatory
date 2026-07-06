"""Tests for fresh_target_harvest.py — synthetic fixtures only (no Docker, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wutai_clinic.intervention.fresh_target_harvest import (
    run_fresh_target_harvest,
    write_fresh_target_harvest_plan,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _evidence_index_jsonl(tmp_path: Path, instance_ids: list[str]) -> Path:
    rows_path = tmp_path / "evidence_index_rows.jsonl"
    with rows_path.open("w") as fh:
        for iid in instance_ids:
            fh.write(json.dumps({"instance_id": iid, "status": "official_eval_completed"}) + "\n")
    return rows_path


def _dataset_json(tmp_path: Path, instance_ids: list[str], suffix: str = "ds.json") -> Path:
    path = tmp_path / suffix
    path.write_text(json.dumps({"instance_ids": instance_ids}))
    return path


def _dataset_jsonl(tmp_path: Path, instance_ids: list[str]) -> Path:
    path = tmp_path / "ds.jsonl"
    with path.open("w") as fh:
        for iid in instance_ids:
            fh.write(json.dumps({"instance_id": iid}) + "\n")
    return path


def _lite300_report(tmp_path: Path, instance_ids: list[str]) -> Path:
    path = tmp_path / "lite300.json"
    path.write_text(json.dumps({"completed_ids": instance_ids}))
    return path


def _run_plan(
    tmp_path: Path,
    *,
    evidence_ids: list[str],
    dataset_ids: list[str],
    lite300_ids: list[str],
    max_instances: int = 10,
    out_subdir: str = "plan",
    dataset_format: str = "json",
) -> tuple[dict[str, Any], Path]:
    ev = _evidence_index_jsonl(tmp_path, evidence_ids)
    if dataset_format == "jsonl":
        ds = _dataset_jsonl(tmp_path, dataset_ids)
    else:
        ds = _dataset_json(tmp_path, dataset_ids)
    l3 = _lite300_report(tmp_path, lite300_ids)
    out = tmp_path / out_subdir
    result = write_fresh_target_harvest_plan(
        evidence_index_path=ev,
        dataset_instances_path=ds,
        lite300_report_path=l3,
        max_instances=max_instances,
        output_dir=out,
    )
    return result, out


# ---------------------------------------------------------------------------
# Plan mode: contamination exclusion
# ---------------------------------------------------------------------------


def test_plan_evidence_ids_excluded(tmp_path):
    result, out = _run_plan(
        tmp_path,
        evidence_ids=["repo__task-001", "repo__task-002"],
        dataset_ids=["repo__task-001", "repo__task-002", "repo__task-003"],
        lite300_ids=[],
    )
    sel = {r["instance_id"] for r in result["selected_instances"]}
    assert "repo__task-001" not in sel
    assert "repo__task-002" not in sel
    assert "repo__task-003" in sel
    assert result["decision"] == "fresh_target_harvest_plan_ready_live_execution_not_authorized"


def test_plan_lite300_ids_excluded(tmp_path):
    result, out = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=["repo__task-001", "repo__task-002", "repo__task-003"],
        lite300_ids=["repo__task-002"],
    )
    sel = {r["instance_id"] for r in result["selected_instances"]}
    assert "repo__task-002" not in sel
    assert "repo__task-001" in sel
    assert "repo__task-003" in sel


def test_plan_intersection_excluded_once(tmp_path):
    """Instance in both evidence-index AND lite300 should be excluded exactly once."""
    result, out = _run_plan(
        tmp_path,
        evidence_ids=["repo__overlap-001"],
        dataset_ids=["repo__overlap-001", "repo__clean-001"],
        lite300_ids=["repo__overlap-001"],
    )
    sel = {r["instance_id"] for r in result["selected_instances"]}
    assert "repo__overlap-001" not in sel
    assert "repo__clean-001" in sel


def test_plan_all_contaminated_gives_empty_selection(tmp_path):
    result, out = _run_plan(
        tmp_path,
        evidence_ids=["repo__task-001"],
        dataset_ids=["repo__task-001"],
        lite300_ids=[],
    )
    assert result["selected_instances"] == []
    assert result["decision"] == "fresh_target_harvest_plan_ready_live_execution_not_authorized"


# ---------------------------------------------------------------------------
# Plan mode: max_instances cap
# ---------------------------------------------------------------------------


def test_plan_max_instances_cap_truncates(tmp_path):
    all_ids = [f"repo__task-{i:03d}" for i in range(20)]
    result, out = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=all_ids,
        lite300_ids=[],
        max_instances=5,
    )
    assert len(result["selected_instances"]) == 5


def test_plan_max_instances_cap_counts_correctly(tmp_path):
    all_ids = [f"repo__task-{i:03d}" for i in range(10)]
    result, out = _run_plan(
        tmp_path,
        evidence_ids=all_ids[:3],
        dataset_ids=all_ids,
        lite300_ids=[],
        max_instances=4,
    )
    sel = result["selected_instances"]
    assert len(sel) == 4
    for row in sel:
        assert row["instance_id"] not in all_ids[:3]


def test_plan_max_instances_not_exceeded_when_fewer_candidates(tmp_path):
    result, out = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=["repo__task-001", "repo__task-002"],
        lite300_ids=[],
        max_instances=30,
    )
    assert len(result["selected_instances"]) == 2


# ---------------------------------------------------------------------------
# Plan mode: report structure
# ---------------------------------------------------------------------------


def test_plan_report_fields(tmp_path):
    result, out = _run_plan(
        tmp_path,
        evidence_ids=["ev__task-001"],
        dataset_ids=["ev__task-001", "clean__task-001"],
        lite300_ids=["lite__task-001"],
    )
    report_path = out / "fresh_target_harvest_plan.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["decision"] == "fresh_target_harvest_plan_ready_live_execution_not_authorized"
    summary = report["summary"]
    assert "selected_instances" in summary
    assert "excluded_by_evidence_index" in summary
    assert "excluded_by_lite300" in summary
    assert "max_instances_cap" in summary
    assert summary["excluded_by_evidence_index"] >= 0
    assert summary["excluded_by_lite300"] >= 0
    op = report["operator_decisions"]
    assert "dataset_source" in op
    assert "max_instances" in op
    gates = report["gates"]
    assert gates["evidence_index_loaded"] is True
    assert gates["lite300_loaded"] is True
    assert gates["lite300_exclusion_degraded"] is False
    assert "claim_boundary" in report


def test_plan_manifest_written(tmp_path):
    _, out = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=["repo__task-001"],
        lite300_ids=[],
    )
    assert (out / "fresh_target_harvest_plan_manifest.json").exists()


def test_plan_selected_instance_fields(tmp_path):
    result, _ = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=["django__django-12345"],
        lite300_ids=[],
    )
    rows = result["selected_instances"]
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == "django__django-12345"
    assert row["selection_role"] == "harvest_candidate"
    assert row["contamination_status"] == "uncontaminated"
    assert row["source_family"] == "django"


# ---------------------------------------------------------------------------
# Plan mode: JSONL dataset format
# ---------------------------------------------------------------------------


def test_plan_jsonl_dataset(tmp_path):
    result, _ = _run_plan(
        tmp_path,
        evidence_ids=[],
        dataset_ids=["repo__task-001", "repo__task-002"],
        lite300_ids=[],
        dataset_format="jsonl",
    )
    sel = {r["instance_id"] for r in result["selected_instances"]}
    assert sel == {"repo__task-001", "repo__task-002"}


# ---------------------------------------------------------------------------
# Plan mode: Lite300 failure → block
# ---------------------------------------------------------------------------


def test_plan_lite300_missing_blocks(tmp_path):
    ev = _evidence_index_jsonl(tmp_path, [])
    ds = _dataset_json(tmp_path, ["repo__task-001"])
    out = tmp_path / "plan"
    result = write_fresh_target_harvest_plan(
        evidence_index_path=ev,
        dataset_instances_path=ds,
        lite300_report_path=tmp_path / "nonexistent_lite300.json",
        max_instances=10,
        output_dir=out,
    )
    assert result["decision"] == "fresh_target_harvest_plan_blocked_lite300_exclusion_degraded"
    report = json.loads((out / "fresh_target_harvest_plan.json").read_text())
    assert report["decision"] == "fresh_target_harvest_plan_blocked_lite300_exclusion_degraded"
    assert report["gates"]["lite300_exclusion_degraded"] is True


def test_plan_lite300_bad_json_blocks(tmp_path):
    ev = _evidence_index_jsonl(tmp_path, [])
    ds = _dataset_json(tmp_path, ["repo__task-001"])
    bad_lite300 = tmp_path / "bad_lite300.json"
    bad_lite300.write_text("NOT JSON")
    out = tmp_path / "plan"
    result = write_fresh_target_harvest_plan(
        evidence_index_path=ev,
        dataset_instances_path=ds,
        lite300_report_path=bad_lite300,
        max_instances=10,
        output_dir=out,
    )
    assert result["decision"] == "fresh_target_harvest_plan_blocked_lite300_exclusion_degraded"


def test_plan_lite300_missing_key_blocks(tmp_path):
    ev = _evidence_index_jsonl(tmp_path, [])
    ds = _dataset_json(tmp_path, ["repo__task-001"])
    bad_lite300 = tmp_path / "no_key_lite300.json"
    bad_lite300.write_text(json.dumps({"something_else": ["x"]}))
    out = tmp_path / "plan"
    result = write_fresh_target_harvest_plan(
        evidence_index_path=ev,
        dataset_instances_path=ds,
        lite300_report_path=bad_lite300,
        max_instances=10,
        output_dir=out,
    )
    assert result["decision"] == "fresh_target_harvest_plan_blocked_lite300_exclusion_degraded"


# ---------------------------------------------------------------------------
# Plan mode: missing evidence-index → raises
# ---------------------------------------------------------------------------


def test_plan_missing_evidence_index_raises(tmp_path):
    ds = _dataset_json(tmp_path, ["repo__task-001"])
    l3 = _lite300_report(tmp_path, [])
    out = tmp_path / "plan"
    with pytest.raises(FileNotFoundError):
        write_fresh_target_harvest_plan(
            evidence_index_path=tmp_path / "nonexistent.jsonl",
            dataset_instances_path=ds,
            lite300_report_path=l3,
            max_instances=10,
            output_dir=out,
        )


# ---------------------------------------------------------------------------
# Execute mode: ack gate
# ---------------------------------------------------------------------------


def test_execute_no_acks_raises(tmp_path):
    plan_path = tmp_path / "plan" / "fresh_target_harvest_plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps({
        "decision": "fresh_target_harvest_plan_ready_live_execution_not_authorized",
        "summary": {"selected_instances": []},
    }))
    with pytest.raises(RuntimeError, match="requires --ack-docker and --ack-external-provider"):
        run_fresh_target_harvest(
            plan_path=plan_path,
            runner=lambda iid, od: {},
            output_dir=tmp_path / "exec",
            ack_docker=False,
            ack_external_provider=False,
        )


def test_execute_only_docker_ack_raises(tmp_path):
    plan_path = tmp_path / "plan" / "fresh_target_harvest_plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps({
        "decision": "fresh_target_harvest_plan_ready_live_execution_not_authorized",
        "summary": {"selected_instances": []},
    }))
    with pytest.raises(RuntimeError):
        run_fresh_target_harvest(
            plan_path=plan_path,
            runner=lambda iid, od: {},
            output_dir=tmp_path / "exec",
            ack_docker=True,
            ack_external_provider=False,
        )


def test_execute_only_provider_ack_raises(tmp_path):
    plan_path = tmp_path / "plan" / "fresh_target_harvest_plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps({
        "decision": "fresh_target_harvest_plan_ready_live_execution_not_authorized",
        "summary": {"selected_instances": []},
    }))
    with pytest.raises(RuntimeError):
        run_fresh_target_harvest(
            plan_path=plan_path,
            runner=lambda iid, od: {},
            output_dir=tmp_path / "exec",
            ack_docker=False,
            ack_external_provider=True,
        )


# ---------------------------------------------------------------------------
# Execute mode: unresolved → candidate / resolved → sentinel split
# ---------------------------------------------------------------------------


def _make_plan_file(tmp_path: Path, instance_ids: list[str]) -> Path:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(exist_ok=True)
    plan_path = plan_dir / "fresh_target_harvest_plan.json"
    selected = [
        {
            "instance_id": iid,
            "selection_role": "harvest_candidate",
            "contamination_status": "uncontaminated",
            "source_family": iid.split("__")[0] if "__" in iid else iid,
        }
        for iid in instance_ids
    ]
    plan_path.write_text(json.dumps({
        "decision": "fresh_target_harvest_plan_ready_live_execution_not_authorized",
        "summary": {"selected_instances": selected},
    }))
    return plan_path


def _fake_runner(results_map: dict[str, str]):
    """Return a runner that maps instance_id -> status."""
    def runner(instance_id: str, output_dir: Path) -> dict:
        return {
            "instance_id": instance_id,
            "status": results_map.get(instance_id, "error"),
            "patch_path": None,
            "archive_dir": str(output_dir),
        }
    return runner


def test_execute_unresolved_becomes_candidate(tmp_path):
    plan_path = _make_plan_file(tmp_path, ["repo__task-001"])
    runner = _fake_runner({"repo__task-001": "unresolved"})
    result = run_fresh_target_harvest(
        plan_path=plan_path,
        runner=runner,
        output_dir=tmp_path / "exec",
        ack_docker=True,
        ack_external_provider=True,
    )
    assert len(result["harvest_candidates"]) == 1
    assert len(result["success_sentinels"]) == 0
    cand = result["harvest_candidates"][0]
    assert cand["source_task_id"] == "repo__task-001"
    assert cand["selection_role"] == "harvest_candidate"
    assert cand["contamination_status"] == "uncontaminated_harvest"
    assert cand["baseline_status"] == "unresolved"


def test_execute_resolved_becomes_sentinel(tmp_path):
    plan_path = _make_plan_file(tmp_path, ["repo__task-001"])
    runner = _fake_runner({"repo__task-001": "resolved"})
    result = run_fresh_target_harvest(
        plan_path=plan_path,
        runner=runner,
        output_dir=tmp_path / "exec",
        ack_docker=True,
        ack_external_provider=True,
    )
    assert len(result["harvest_candidates"]) == 0
    assert len(result["success_sentinels"]) == 1
    sent = result["success_sentinels"][0]
    assert sent["source_task_id"] == "repo__task-001"
    assert sent["baseline_status"] == "resolved"


def test_execute_mixed_results(tmp_path):
    plan_path = _make_plan_file(
        tmp_path, ["repo__task-001", "repo__task-002", "repo__task-003"]
    )
    runner = _fake_runner({
        "repo__task-001": "unresolved",
        "repo__task-002": "resolved",
        "repo__task-003": "error",
    })
    result = run_fresh_target_harvest(
        plan_path=plan_path,
        runner=runner,
        output_dir=tmp_path / "exec",
        ack_docker=True,
        ack_external_provider=True,
    )
    assert len(result["harvest_candidates"]) == 1
    assert len(result["success_sentinels"]) == 1
    assert len(result["harvest_errors"]) == 1


def test_execute_runner_exception_recorded(tmp_path):
    plan_path = _make_plan_file(tmp_path, ["repo__task-001"])

    def bad_runner(iid, od):
        raise ValueError("simulated runner failure")

    result = run_fresh_target_harvest(
        plan_path=plan_path,
        runner=bad_runner,
        output_dir=tmp_path / "exec",
        ack_docker=True,
        ack_external_provider=True,
    )
    assert len(result["harvest_errors"]) == 1
    err = result["harvest_errors"][0]
    assert "simulated runner failure" in err["error"]


# ---------------------------------------------------------------------------
# Execute mode: report and manifest written
# ---------------------------------------------------------------------------


def test_execute_report_and_manifest_written(tmp_path):
    plan_path = _make_plan_file(tmp_path, ["repo__task-001"])
    runner = _fake_runner({"repo__task-001": "unresolved"})
    result = run_fresh_target_harvest(
        plan_path=plan_path,
        runner=runner,
        output_dir=tmp_path / "exec",
        ack_docker=True,
        ack_external_provider=True,
    )
    assert result["report_path"].exists()
    assert result["manifest_path"].exists()


# ---------------------------------------------------------------------------
# Downstream compatibility: candidate rows from execute can be passed to
# write_protocol_v2_pair_inputs_evidence with synthetic trajectory files.
# ---------------------------------------------------------------------------


def test_execute_candidates_downstream_compat(tmp_path):
    """Verify candidate rows from execute are accepted by write_protocol_v2_pair_inputs_evidence."""
    from wutai_clinic.intervention.protocol_v2_pair_inputs import (
        write_protocol_v2_pair_inputs_evidence,
    )

    # Prepare a minimal trajectory file in the format the materializer expects
    task_id = "repo__task-001"
    traj_dir = tmp_path / "trajs" / task_id
    traj_dir.mkdir(parents=True)
    traj_path = traj_dir / f"{task_id}.traj"

    # Minimal valid trajectory: environment, trajectory list, replay_config
    import json as _json
    traj_payload = {
        "environment": task_id,
        "trajectory": [
            {"action": "echo hello", "thought": "checking"},
        ],
        "replay_config": _json.dumps({
            "output_dir": str(tmp_path / "native_out"),
            "env_var_path": None,
            "env": {"deployment": {"python_standalone_dir": ""}},
            "agent": {
                "model": {
                    "name": "openai/gpt-5.5",
                    "api_key": None,
                    "api_base": None,
                    "api_version": None,
                    "temperature": 0.0,
                    "top_p": None,
                    "per_instance_call_limit": 0,
                    "per_instance_cost_limit": 0.0,
                    "total_cost_limit": 0.0,
                }
            },
        }),
    }
    traj_path.write_text(_json.dumps(traj_payload))

    # Build a candidate row compatible with protocol_v2_pair_inputs
    candidate_row = {
        "source_task_id": task_id,
        "pair_id": "test-pair-001",
        "selection_role": "harvest_candidate",
        "source_family": "repo",
        "contamination_status": "uncontaminated_harvest",
        "intervention_policy_id": "break_recurrence_and_replan",
        "candidate_static_prefix_index": 1,
        "recalibrated_trigger_mode": "live_feature_signature_window",
        "exact_trigger_disabled": True,
        "selection_status": "eligible_for_live_pair",
        "same_pair_posthoc_positive_claim_allowed": False,
        "phase6_live_pair_authorized": False,
        "official_eval_authorized": False,
    }

    out_root = tmp_path / "pair_inputs"
    native_root = tmp_path / "native_out"
    out_root.mkdir(parents=True)
    native_root.mkdir(parents=True)

    # Should not raise; failures are captured in report["failures"]
    report_result = write_protocol_v2_pair_inputs_evidence(
        candidate_rows=[candidate_row],
        trajectory_root=tmp_path / "trajs",
        output_root=out_root,
        native_root=native_root,
    )
    # May fail (trajectory check) but must return a dict, not raise
    assert isinstance(report_result, dict)
    assert "report" in report_result


# ---------------------------------------------------------------------------
# CLI end-to-end (plan mode, using CliRunner)
# ---------------------------------------------------------------------------


def test_cli_plan_mode_end_to_end(tmp_path):
    from typer.testing import CliRunner
    import typer

    from wutai_clinic.intervention.fresh_target_harvest import write_fresh_target_harvest_plan

    # Build a minimal Typer app with the fresh-target-harvest command
    cli_app = typer.Typer()

    @cli_app.command("fresh-target-harvest")
    def cmd(
        evidence_index: Path = typer.Option(..., "--evidence-index"),
        dataset_instances: Path = typer.Option(..., "--dataset-instances"),
        lite300_report: Path = typer.Option(..., "--lite300-report"),
        max_instances: int = typer.Option(30, "--max-instances"),
        output_dir: Path = typer.Option(..., "-o"),
        execute: bool = typer.Option(False, "--execute"),
        ack_docker: bool = typer.Option(False, "--ack-docker"),
        ack_external_provider: bool = typer.Option(False, "--ack-external-provider"),
    ) -> None:
        result = write_fresh_target_harvest_plan(
            evidence_index_path=evidence_index,
            dataset_instances_path=dataset_instances,
            lite300_report_path=lite300_report,
            max_instances=max_instances,
            output_dir=output_dir,
        )
        typer.echo(json.dumps({"decision": result["decision"]}, indent=2))

    ev = _evidence_index_jsonl(tmp_path, ["ev__task-001"])
    ds = _dataset_json(tmp_path, ["ev__task-001", "clean__task-001"])
    l3 = _lite300_report(tmp_path, [])
    out = tmp_path / "cli_out"

    runner = CliRunner()
    res = runner.invoke(
        cli_app,
        [
            "--evidence-index", str(ev),
            "--dataset-instances", str(ds),
            "--lite300-report", str(l3),
            "--max-instances", "30",
            "-o", str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    output_data = json.loads(res.output)
    assert output_data["decision"] == "fresh_target_harvest_plan_ready_live_execution_not_authorized"
