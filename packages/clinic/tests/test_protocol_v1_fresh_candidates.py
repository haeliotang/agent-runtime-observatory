from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1_fresh_candidates import (
    protocol_v1_fresh_candidate_report,
    write_protocol_v1_fresh_candidate_evidence,
)
from wutai_clinic.io import count_jsonl, read_jsonl

runner = CliRunner()


def _eligible_ref(pair_id: str, task_id: str, *, rank: int, role: str) -> dict:
    return {
        "selection_status": "eligible_for_live_pair",
        "next_batch_rank": rank,
        "pair_id": pair_id,
        "source_task_id": task_id,
        "source_family": task_id.split("__", maxsplit=1)[0],
        "selection_role": role,
        "intervention_policy_id": (
            "same_action_escape" if "same" in pair_id else "error_observation_recovery"
        ),
        "candidate_prefix_index": 3 + rank,
        "candidate_ref_sha256": f"ref-{rank}",
        "replay_risk_level": "no_known_replay_nondeterminism_patterns",
        "replay_risk_counts": {},
    }


def _eligible_refs() -> list[dict]:
    return [
        _eligible_ref("used-error", "repo__used_error", rank=1, role="failure_target"),
        _eligible_ref("used-same", "repo__used_same", rank=2, role="failure_target"),
        _eligible_ref("fresh-error", "repo__fresh_error", rank=3, role="failure_target"),
        _eligible_ref("fresh-sentinel", "repo__fresh_sentinel", rank=4, role="success_sentinel"),
        _eligible_ref("fresh-same-sentinel", "repo__fresh_same", rank=5, role="success_sentinel"),
    ]


def _pool_report() -> dict:
    return {
        "decision": "phase62_low_nondeterminism_candidate_pool_ready_with_eligible_refs",
        "summary": {
            "eligible_count": 5,
            "status_counts": {"eligible_for_live_pair": 5},
        },
    }


def _protocol_v1_plan() -> dict:
    return {
        "decision": "protocol_v1_plan_ready_not_live_executed",
        "pair_count": 2,
        "pairs": [
            {"pair_id": "used-error", "source_task_id": "repo__used_error"},
            {"pair_id": "used-same", "source_task_id": "repo__used_same"},
        ],
    }


def _no_uplift_diagnosis() -> dict:
    return {
        "decision": "phase6_no_uplift_diagnosis_complete",
        "per_pair": [
            {
                "pair_id": "used-error",
                "source_task_id": "repo__used_error",
                "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            }
        ],
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_protocol_v1_fresh_gate_excludes_used_pairs_and_marks_underpowered() -> None:
    report = protocol_v1_fresh_candidate_report(
        eligible_refs=_eligible_refs(),
        candidate_pool_report=_pool_report(),
        protocol_v1_plan=_protocol_v1_plan(),
        no_uplift_diagnosis=_no_uplift_diagnosis(),
        target_pair_count=4,
    )

    assert report["passed"] is True
    assert (
        report["decision"]
        == "protocol_v1_fresh_candidate_set_ready_limited_underpowered_no_batch_claim"
    )
    assert report["summary"]["fresh_candidate_count"] == 3
    assert report["summary"]["fresh_failure_target_count"] == 1
    assert report["summary"]["contaminated_excluded_count"] == 2
    assert {row["pair_id"] for row in report["fresh_candidates"]} == {
        "fresh-error",
        "fresh-sentinel",
        "fresh-same-sentinel",
    }
    assert all(
        row["exclusion_reason"] == "same_pair_posthoc_official_eval_contaminated"
        for row in report["excluded_candidates"]
    )
    policy = report["continuation_policy"]
    assert policy["allow_protocol_v1_live_single_planned_preflight"] is True
    assert policy["allow_protocol_v1_full_batch_planned_preflight"] is False
    assert policy["allow_state_capsule_input_preparation"] is True
    assert policy["allow_live_hook_preflight"] is True
    assert policy["allow_batch3_real_run"] is False
    assert policy["allow_phase6_live_pair_real_run"] is False
    assert policy["allow_protocol_v1_real_run"] is False
    assert policy["allow_positive_uplift_claim"] is False


def test_protocol_v1_fresh_gate_blocks_when_all_candidates_are_contaminated() -> None:
    report = protocol_v1_fresh_candidate_report(
        eligible_refs=[_eligible_ref("used-error", "repo__used_error", rank=1, role="failure_target")],
        candidate_pool_report={"decision": _pool_report()["decision"], "summary": {"eligible_count": 1}},
        protocol_v1_plan=_protocol_v1_plan(),
        no_uplift_diagnosis=_no_uplift_diagnosis(),
        target_pair_count=1,
    )

    assert report["passed"] is False
    assert report["decision"] == "protocol_v1_fresh_candidate_set_blocked_no_fresh_candidates"
    assert report["gates"]["at_least_one_fresh_candidate"] is False
    assert report["continuation_policy"]["allow_protocol_v1_live_single_planned_preflight"] is False


def test_write_protocol_v1_fresh_candidate_evidence_artifacts(tmp_path: Path) -> None:
    refs_path = tmp_path / "eligible_refs.jsonl"
    pool_path = tmp_path / "pool.json"
    plan_path = tmp_path / "plan.json"
    diagnosis_path = tmp_path / "diagnosis.json"
    _write_jsonl(refs_path, _eligible_refs())
    _write_json(pool_path, _pool_report())
    _write_json(plan_path, _protocol_v1_plan())
    _write_json(diagnosis_path, _no_uplift_diagnosis())

    result = write_protocol_v1_fresh_candidate_evidence(
        eligible_refs=_eligible_refs(),
        candidate_pool_report=_pool_report(),
        protocol_v1_plan=_protocol_v1_plan(),
        no_uplift_diagnosis=_no_uplift_diagnosis(),
        output_dir=tmp_path / "fresh",
        input_artifacts=[refs_path, pool_path, plan_path, diagnosis_path],
        target_pair_count=4,
    )

    assert result["report"]["passed"] is True
    assert count_jsonl(result["fresh_path"]) == 3
    assert count_jsonl(result["excluded_path"]) == 2
    assert result["manifest"]["passed"] is True
    assert len(result["manifest"]["artifacts"]) == 8
    fresh_rows = list(read_jsonl(result["fresh_path"]))
    assert fresh_rows[0]["protocol_v1_constraint_hook_required"] is True
    assert fresh_rows[0]["candidate_static_prefix_index"] == fresh_rows[0]["candidate_prefix_index"]
    assert fresh_rows[0]["recalibrated_trigger_mode"] == "live_feature_signature_window"
    assert fresh_rows[0]["exact_static_prefix_trigger_disabled"] is True
    assert fresh_rows[0]["batch3_real_run_authorized"] is False


def test_cli_protocol_v1_fresh_candidates_writes_package(tmp_path: Path) -> None:
    refs_path = tmp_path / "eligible_refs.jsonl"
    pool_path = tmp_path / "pool.json"
    plan_path = tmp_path / "plan.json"
    diagnosis_path = tmp_path / "diagnosis.json"
    output_dir = tmp_path / "fresh-cli"
    _write_jsonl(refs_path, _eligible_refs())
    _write_json(pool_path, _pool_report())
    _write_json(plan_path, _protocol_v1_plan())
    _write_json(diagnosis_path, _no_uplift_diagnosis())

    result = runner.invoke(
        app,
        [
            "protocol-v1-fresh-candidates",
            str(refs_path),
            str(pool_path),
            str(plan_path),
            str(diagnosis_path),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert (
        payload["decision"]
        == "protocol_v1_fresh_candidate_set_ready_limited_underpowered_no_batch_claim"
    )
    assert payload["passed"] is True
    assert payload["fresh_candidate_count"] == 3
    assert payload["fresh_failure_target_count"] == 1
    assert payload["allow_protocol_v1_live_single_planned_preflight"] is True
    assert payload["allow_protocol_v1_full_batch_planned_preflight"] is False
    assert (output_dir / "protocol_v1_fresh_candidate_set_report.json").exists()
