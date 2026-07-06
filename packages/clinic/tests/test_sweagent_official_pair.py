from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live_pair import SWEAgentLivePairSpec, run_sweagent_live_pair
from wutai_clinic.adapters.sweagent_official_pair import (
    SWEAgentOfficialPairSpec,
    run_sweagent_official_pair,
)
from wutai_clinic.adapters.sweagent_phase6_official_eval import (
    SWEAgentPhase6OfficialEvalSpec,
    run_sweagent_phase6_official_eval,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.paired_fork import default_protocol
from wutai_clinic.intervention.replay_protocol import StateCapsule
from wutai_clinic.io import read_jsonl

runner = CliRunner()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    )


def _official_fixture(tmp_path: Path) -> tuple[Path, Path]:
    pair_id = "phase312_pair_001_failure_target_insert_validation_checkpoint"
    source_task_id = "sympy__sympy-21627"
    pair_summary = tmp_path / "pair_summary.jsonl"
    report = tmp_path / "official_report.json"
    control_report = tmp_path / "reports" / "control_report.json"
    treatment_report = tmp_path / "reports" / "treatment_report.json"
    control_prediction = tmp_path / "predictions" / "control.jsonl"
    treatment_prediction = tmp_path / "predictions" / "treatment.jsonl"
    _write_json(control_report, {source_task_id: {"resolved": False}})
    _write_json(treatment_report, {source_task_id: {"resolved": True}})
    _write_jsonl(
        control_prediction,
        [{"instance_id": source_task_id, "model_name_or_path": "control", "model_patch": "diff"}],
    )
    _write_jsonl(
        treatment_prediction,
        [
            {
                "instance_id": source_task_id,
                "model_name_or_path": "intervention",
                "model_patch": "diff",
            }
        ],
    )
    _write_jsonl(
        pair_summary,
        [
            {
                "control_resolved": False,
                "effect_label": "intervention_only_resolved_trigger_hit_candidate",
                "intervention_injected_once": True,
                "intervention_resolved": True,
                "intervention_treatment_status": "treated_injected_once",
                "main_attribution_eligible": True,
                "official_eval_completed": True,
                "pair_eval_scope": "main_treatment_attribution_candidate",
                "pair_id": pair_id,
                "patches_applied": True,
                "source_task_id": source_task_id,
            }
        ],
    )
    _write_json(
        report,
        {
            "passed": True,
            "official_eval_started": True,
            "arm_reports": [
                {
                    "arm_type": "control",
                    "completed": True,
                    "official_report_path": control_report.as_posix(),
                    "official_report_sha256": _sha256(control_report),
                    "pair_id": pair_id,
                    "patch_successfully_applied": True,
                    "prediction_path": control_prediction.as_posix(),
                    "prediction_sha256": _sha256(control_prediction),
                    "resolved": False,
                    "source_task_id": source_task_id,
                },
                {
                    "arm_type": "intervention",
                    "completed": True,
                    "official_report_path": treatment_report.as_posix(),
                    "official_report_sha256": _sha256(treatment_report),
                    "pair_id": pair_id,
                    "patch_successfully_applied": True,
                    "prediction_path": treatment_prediction.as_posix(),
                    "prediction_sha256": _sha256(treatment_prediction),
                    "resolved": True,
                    "source_task_id": source_task_id,
                },
            ],
        },
    )
    return pair_summary, report


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capsule_payload() -> dict:
    capsule = StateCapsule.from_dict(
        {
            "task_id": "sweagent_live_single_pair",
            "repo_hash": "repo-sha",
            "agent_config_hash": "agent-sha",
            "provider_config_hash": "provider-sha",
            "message_prefix_hash": "prefix-sha",
            "working_tree_diff_hash": "diff-sha",
            "observation_window_hash": "obs-sha",
            "model_request_hash": "model-sha",
            "runner_config_hash": "runner-sha",
            "deployment_hash": "deploy-sha",
            "replay_config_hash": "replay-sha",
            "runtime_nondeterminism_policy": "live_run_single_sequential_replay_temperature_zero",
            "metadata": {"raw_probe_payload_logged": False, "mode": "live"},
        }
    )
    return capsule.to_dict()


def _write_live_single_dir(
    root: Path,
    *,
    arm_type: str,
    source_task_id: str,
    patch_text: str,
    reference: str | None = None,
) -> None:
    root.mkdir(parents=True)
    capsule = _capsule_payload()
    patch_path = root / "sweagent_live_single.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    _write_json(root / "sweagent_live_single_capsule.json", capsule)
    _write_json(root / "sweagent_live_single_protocol.json", default_protocol().to_dict())
    _write_json(root / "sweagent_live_single_features.json", {"error_streak": 3})
    _write_json(
        root / "sweagent_live_single_report.json",
        {
            "decision": "sweagent_live_single_run_completed",
            "passed": True,
            "arm_type": arm_type,
            "run_single_started": True,
            "capsule_fingerprint": capsule["fingerprint"],
            "reference_capsule_fingerprint": reference,
            "injection_count": 1 if arm_type == "treatment" else 0,
            "source_task_id": source_task_id,
            "patch_archived": True,
            "patch_archive_path": patch_path.as_posix(),
            "patch_archive_sha256": _sha256(patch_path),
        },
    )
    event = {
        "event_type": "capsule_hook",
        "fork_decision": "state_capsule_equivalent"
        if arm_type == "treatment"
        else "state_capsule_materialized_no_reference",
        "fork_passed": True,
        "injected": arm_type == "treatment",
        "raw_payload_logged": False,
        "trigger_hit": True,
    }
    (root / "sweagent_live_single_events.jsonl").write_text(json.dumps(event) + "\n")


def _write_phase5_live_package(tmp_path: Path) -> Path:
    live_dir = tmp_path / "phase5_live"
    source_task_id = "django__django-14667"
    pair_id = "phase6_pair_001"
    fingerprint = _capsule_payload()["fingerprint"]
    _write_live_single_dir(
        live_dir / "control",
        arm_type="control",
        source_task_id=source_task_id,
        patch_text="diff --git a/control.py b/control.py\n",
    )
    _write_live_single_dir(
        live_dir / "treatment",
        arm_type="treatment",
        source_task_id=source_task_id,
        patch_text="diff --git a/treatment.py b/treatment.py\n",
        reference=fingerprint,
    )
    run_sweagent_live_pair(
        spec=SWEAgentLivePairSpec(
            control_dir=live_dir / "control",
            treatment_dir=live_dir / "treatment",
            output_dir=live_dir / "pair",
        )
    )
    _write_json(
        live_dir / "live_hook_preflight_report.json",
        {
            "decision": "sweagent_live_hook_preflight_pair_ready_pending_official_eval",
            "passed": True,
            "pair_id": pair_id,
            "source_task_id": source_task_id,
        },
    )
    return live_dir


def _write_phase6_raw_official_reports(
    eval_dir: Path,
    *,
    run_id: str,
    pair_id: str,
    source_task_id: str,
) -> None:
    for arm_type, resolved in [("control", False), ("intervention", True)]:
        model_name = f"phase6_official_eval__{pair_id}__{arm_type}"
        report_path = (
            eval_dir / "logs/run_evaluation" / run_id / model_name / source_task_id / "report.json"
        )
        _write_json(
            report_path,
            {
                source_task_id: {
                    "patch_successfully_applied": True,
                    "resolved": resolved,
                    "tests_status": {},
                }
            },
        )


def test_sweagent_official_pair_imports_resolved_outcome(tmp_path: Path) -> None:
    pair_summary, official_report = _official_fixture(tmp_path)

    result = run_sweagent_official_pair(
        spec=SWEAgentOfficialPairSpec(
            pair_summary_path=pair_summary,
            official_eval_report=official_report,
            output_dir=tmp_path / "out",
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=True),
    )

    report = result["report"]
    summary = list(read_jsonl(result["summary_path"]))[0]
    assert report["passed"] is True
    assert report["decision"] == "sweagent_official_pair_outcome_label_ready"
    assert report["source_task_id"] == "sympy__sympy-21627"
    assert report["control_resolved"] is False
    assert report["treatment_resolved"] is True
    assert report["effect_label"] == "intervention_only_resolved_trigger_hit_candidate"
    assert report["state_capsule_equivalence_claimed"] is False
    assert summary["official_eval_completed"] is True


def test_sweagent_official_pair_requires_official_eval_ack(tmp_path: Path) -> None:
    pair_summary, official_report = _official_fixture(tmp_path)

    result = run_sweagent_official_pair(
        spec=SWEAgentOfficialPairSpec(
            pair_summary_path=pair_summary,
            official_eval_report=official_report,
            output_dir=tmp_path / "out",
        )
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "sweagent_official_pair_blocked"
    assert result["report"]["blocking_failures"] == ["official_eval_acknowledged"]


def test_cli_sweagent_official_pair_writes_package(tmp_path: Path) -> None:
    pair_summary, official_report = _official_fixture(tmp_path)
    output_dir = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "sweagent-official-pair",
            str(pair_summary),
            str(official_report),
            "-o",
            str(output_dir),
            "--ack-official-eval",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "sweagent_official_pair_outcome_label_ready"
    assert payload["control_resolved"] is False
    assert payload["treatment_resolved"] is True
    assert (output_dir / "sweagent_official_pair_report.json").exists()
    assert (output_dir / "sweagent_official_pair_summary.jsonl").exists()


def test_phase6_official_eval_imports_cached_reports_and_finalizes_pair(
    tmp_path: Path,
) -> None:
    live_dir = _write_phase5_live_package(tmp_path)
    eval_dir = tmp_path / "eval"
    run_id = "phase6_test_eval"
    _write_phase6_raw_official_reports(
        eval_dir,
        run_id=run_id,
        pair_id="phase6_pair_001",
        source_task_id="django__django-14667",
    )

    result = run_sweagent_phase6_official_eval(
        spec=SWEAgentPhase6OfficialEvalSpec(
            live_preflight_dir=live_dir,
            output_dir=tmp_path / "phase6",
            eval_dir=eval_dir,
            run_id=run_id,
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=True),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "phase6_official_eval_outcome_label_ready"
    assert report["official_eval_completed"] is True
    assert report["effect_label"] == "intervention_only_resolved_trigger_hit_candidate"
    assert result["dual_scorecard"]["control_resolved"] is False
    assert result["dual_scorecard"]["treatment_resolved"] is True
    assert (
        result["final_pair_result"]["report"]["decision"]
        == "sweagent_live_pair_outcome_label_ready"
    )
    assert (tmp_path / "phase6" / "predictions" / "control_django__django-14667.jsonl").exists()
    assert (tmp_path / "phase6" / "phase6_official_eval_manifest.json").exists()


def test_phase6_official_eval_blocks_without_patch_archive(tmp_path: Path) -> None:
    live_dir = _write_phase5_live_package(tmp_path)
    (live_dir / "control" / "sweagent_live_single.patch").unlink()

    result = run_sweagent_phase6_official_eval(
        spec=SWEAgentPhase6OfficialEvalSpec(
            live_preflight_dir=live_dir,
            output_dir=tmp_path / "phase6",
        ),
        policy=RuntimePermissionPolicy(),
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "phase6_official_eval_blocked"
    assert "control_patch_archive_present" in result["report"]["blocking_failures"]


def test_phase6_official_eval_requires_ack_to_import_cached_outcomes(tmp_path: Path) -> None:
    live_dir = _write_phase5_live_package(tmp_path)
    eval_dir = tmp_path / "eval"
    run_id = "phase6_test_eval"
    _write_phase6_raw_official_reports(
        eval_dir,
        run_id=run_id,
        pair_id="phase6_pair_001",
        source_task_id="django__django-14667",
    )

    result = run_sweagent_phase6_official_eval(
        spec=SWEAgentPhase6OfficialEvalSpec(
            live_preflight_dir=live_dir,
            output_dir=tmp_path / "phase6",
            eval_dir=eval_dir,
            run_id=run_id,
        )
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "phase6_official_eval_blocked"
    assert "official_eval_ack_if_run_or_import" in result["report"]["blocking_failures"]


def test_cli_phase6_official_eval_writes_package(tmp_path: Path) -> None:
    live_dir = _write_phase5_live_package(tmp_path)
    eval_dir = tmp_path / "eval"
    run_id = "phase6_test_eval"
    _write_phase6_raw_official_reports(
        eval_dir,
        run_id=run_id,
        pair_id="phase6_pair_001",
        source_task_id="django__django-14667",
    )
    output_dir = tmp_path / "phase6"

    result = runner.invoke(
        app,
        [
            "phase6-official-eval",
            str(live_dir),
            "-o",
            str(output_dir),
            "--eval-dir",
            str(eval_dir),
            "--run-id",
            run_id,
            "--ack-official-eval",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "phase6_official_eval_outcome_label_ready"
    assert payload["official_eval_completed"] is True
    assert payload["effect_label"] == "intervention_only_resolved_trigger_hit_candidate"
    assert (output_dir / "phase6_official_eval_report.json").exists()
    assert (output_dir / "phase6_dual_scorecard.json").exists()
