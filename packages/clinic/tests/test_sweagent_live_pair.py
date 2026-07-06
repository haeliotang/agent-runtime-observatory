from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live_pair import SWEAgentLivePairSpec, run_sweagent_live_pair
from wutai_clinic.cli import app
from wutai_clinic.intervention.paired_fork import default_protocol
from wutai_clinic.intervention.replay_protocol import StateCapsule
from wutai_clinic.io import read_jsonl

runner = CliRunner()


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


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_live_single_dir(root: Path, *, arm_type: str, reference: str | None = None) -> None:
    root.mkdir(parents=True)
    capsule = _capsule_payload()
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


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    control = tmp_path / "control"
    treatment = tmp_path / "treatment"
    fingerprint = _capsule_payload()["fingerprint"]
    _write_live_single_dir(control, arm_type="control")
    _write_live_single_dir(treatment, arm_type="treatment", reference=fingerprint)
    return control, treatment


def test_sweagent_live_pair_pending_without_outcomes(tmp_path: Path) -> None:
    control, treatment = _write_pair(tmp_path)

    result = run_sweagent_live_pair(
        spec=SWEAgentLivePairSpec(
            control_dir=control,
            treatment_dir=treatment,
            output_dir=tmp_path / "pair",
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "sweagent_live_pair_ready_pending_official_eval"
    assert report["effect_label"] == "pending_or_incomplete"
    assert report["fork_equivalence"]["passed"] is True
    assert result["manifest_path"].exists()
    assert list(read_jsonl(result["summary_path"]))[0]["main_attribution_eligible"] is True


def test_sweagent_live_pair_operator_outcome_emits_single_pair_label(tmp_path: Path) -> None:
    control, treatment = _write_pair(tmp_path)

    result = run_sweagent_live_pair(
        spec=SWEAgentLivePairSpec(
            control_dir=control,
            treatment_dir=treatment,
            output_dir=tmp_path / "pair",
            control_resolved=False,
            treatment_resolved=True,
            outcome_source="operator_supplied",
        )
    )

    report = result["report"]
    summary = list(read_jsonl(result["summary_path"]))[0]
    assert report["passed"] is True
    assert report["decision"] == "sweagent_live_pair_outcome_label_ready"
    assert report["effect_label"] == "intervention_only_resolved_trigger_hit_candidate"
    assert summary["control_resolved"] is False
    assert summary["intervention_resolved"] is True
    assert summary["single_pair_only"] is True


def test_sweagent_live_pair_blocks_official_eval_source_without_ack(tmp_path: Path) -> None:
    control, treatment = _write_pair(tmp_path)

    result = run_sweagent_live_pair(
        spec=SWEAgentLivePairSpec(
            control_dir=control,
            treatment_dir=treatment,
            output_dir=tmp_path / "pair",
            control_resolved=False,
            treatment_resolved=True,
            outcome_source="official_eval",
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=False),
    )

    report = result["report"]
    assert report["passed"] is False
    assert report["decision"] == "sweagent_live_pair_blocked"
    assert report["blocking_failures"] == ["official_eval_ack_if_claimed"]


def test_cli_sweagent_live_pair_writes_pair_package(tmp_path: Path) -> None:
    control, treatment = _write_pair(tmp_path)
    output_dir = tmp_path / "pair"

    result = runner.invoke(
        app,
        [
            "sweagent-live-pair",
            str(control),
            str(treatment),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "sweagent_live_pair_ready_pending_official_eval"
    assert (output_dir / "sweagent_live_pair_report.json").exists()
    assert (output_dir / "sweagent_live_pair_summary.jsonl").exists()
