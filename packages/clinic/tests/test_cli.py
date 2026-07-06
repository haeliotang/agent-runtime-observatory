from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.io import count_jsonl

from conftest import requires_monorepo

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
runner = CliRunner()


@requires_monorepo
def test_cli_diagnose_writes_legacy_candidates(tmp_path: Path) -> None:
    output = tmp_path / "diagnosis.jsonl"

    result = runner.invoke(
        app,
        [
            "diagnose",
            str(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"),
            "--legacy-candidates",
            "--limit",
            "2",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert count_jsonl(output) == 2


@requires_monorepo
def test_cli_analyze_outputs_report(tmp_path: Path) -> None:
    output = tmp_path / "analysis.json"

    result = runner.invoke(
        app,
        ["analyze", str(MODELS / "trajectories_purified.jsonl"), "--limit", "2", "-o", str(output)],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    assert report["total_trajectories"] == 2
    assert "metrics" in report


@requires_monorepo
def test_cli_prune_writes_jsonl(tmp_path: Path) -> None:
    output = tmp_path / "pruned.jsonl"

    result = runner.invoke(
        app,
        [
            "prune",
            str(MODELS / "trajectories_purified.jsonl"),
            "--limit",
            "3",
            "--no-dedup",
            "--no-target-hygiene",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert count_jsonl(output) == 3
    assert json.loads(result.output)["output_count"] == 3


@requires_monorepo
def test_cli_prune_target_hygiene_matches_legacy_ranked_count(tmp_path: Path) -> None:
    output = tmp_path / "hygienic_ranked.jsonl"

    result = runner.invoke(
        app,
        [
            "prune",
            str(MODELS / "trajectories_purified.jsonl"),
            "--no-dedup",
            "--rank",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert count_jsonl(output) == 9210
    assert payload["hygiene"]["total_purified"] == 9210
    assert payload["hygiene"]["total_filtered"] == 2540


@requires_monorepo
def test_cli_scorecard_phase3a_report_outputs_known_values(tmp_path: Path) -> None:
    output = tmp_path / "scorecard.json"

    result = runner.invoke(
        app,
        [
            "scorecard",
            str(MODELS / "phase3a_controlled_regression_gate_report.json"),
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["passed"] is True
    assert payload["native"]["native_text_route_count"] == 16


@requires_monorepo
def test_cli_intervene_plan_writes_arms(tmp_path: Path) -> None:
    output = tmp_path / "arms.jsonl"

    result = runner.invoke(
        app,
        [
            "intervene",
            str(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"),
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert count_jsonl(output) == 64
    assert json.loads(result.output)["dry_run"] is True


@requires_monorepo
def test_cli_intervene_attribute_writes_report(tmp_path: Path) -> None:
    output = tmp_path / "attribution.json"

    result = runner.invoke(
        app,
        [
            "intervene",
            str(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
            "--mode",
            "attribute",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["classification_counts"]["main_treatment"] == 2


@requires_monorepo
def test_cli_closed_loop_writes_evidence_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "closed-loop"

    result = runner.invoke(
        app,
        [
            "closed-loop",
            str(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"),
            str(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
            str(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
            "--cumulative-report",
            str(MODELS / "phase316_cumulative_diagnosis_report.json"),
            "--trigger-policy-review",
            str(MODELS / "phase316_trigger_policy_review_report.json"),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "closed_loop_trigger_policy_recalibration_required_before_batch3"
    assert count_jsonl(output_dir / "closed_loop_pairs.jsonl") == 32
    assert count_jsonl(output_dir / "closed_loop_arms.jsonl") == 64


def test_cli_protocol_check_validates_capsule_equivalence(tmp_path: Path) -> None:
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(
        "\n".join(
            [
                "trigger:",
                "  type: live_feature",
                "  predicate: error_streak >= 3",
                "action:",
                "  type: inject_system_prompt",
                "  message_id: break_recurrence_and_replan",
                "guard:",
                "  debounce: once_per_pair",
                "  raw_payload_logging: false",
                "claim:",
                "  allowed: bounded_next_step_control",
            ]
        )
        + "\n"
    )
    capsule = {
        "task_id": "sympy__sympy-21627",
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
        "runtime_nondeterminism_policy": "single_worker_temperature_zero",
    }
    control = tmp_path / "control.json"
    treatment = tmp_path / "treatment.json"
    windows = tmp_path / "windows.jsonl"
    control.write_text(json.dumps(capsule) + "\n")
    treatment.write_text(json.dumps(capsule) + "\n")
    windows.write_text(json.dumps({"error_streak": 3}) + "\n")

    result = runner.invoke(
        app,
        [
            "protocol-check",
            str(protocol),
            str(control),
            str(treatment),
            "--feature-windows",
            str(windows),
            "--control-resolved",
            "false",
            "--treatment-resolved",
            "true",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "intervention_only_resolved_trigger_hit_candidate"


def test_cli_sweagent_preflight_writes_evidence_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "sweagent-preflight"

    result = runner.invoke(app, ["sweagent-preflight", "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "sweagent_adapter_preflight_ready_no_real_run"
    assert (output_dir / "sweagent_preflight_report.json").exists()
    assert (output_dir / "sweagent_preflight_manifest.json").exists()
    assert count_jsonl(output_dir / "sweagent_preflight_events.jsonl") == 6


def test_cli_sweagent_live_plan_requires_explicit_acks(tmp_path: Path) -> None:
    blocked = runner.invoke(app, ["sweagent-live-plan"])
    output = tmp_path / "live-plan.json"
    allowed = runner.invoke(
        app,
        [
            "sweagent-live-plan",
            "--ack-docker",
            "--ack-external-provider",
            "-o",
            str(output),
        ],
    )

    assert blocked.exit_code == 0, blocked.output
    blocked_payload = json.loads(blocked.output)
    assert blocked_payload["passed"] is False
    assert blocked_payload["decision"] == "sweagent_run_single_live_blocked_needs_ack"
    assert allowed.exit_code == 0, allowed.output
    allowed_payload = json.loads(output.read_text())
    assert allowed_payload["passed"] is True
    assert allowed_payload["decision"] == "sweagent_run_single_live_authorized"


def test_cli_sweagent_live_single_plans_without_execution(tmp_path: Path) -> None:
    config = tmp_path / "run_single.yaml"
    config.write_text("agent:\n  model:\n    name: fake\n", encoding="utf-8")
    output_dir = tmp_path / "live-single"

    result = runner.invoke(app, ["sweagent-live-single", str(config), "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "sweagent_live_single_planned_no_run"
    assert payload["execute_requested"] is False
    assert payload["run_single_started"] is False
    assert (output_dir / "sweagent_live_single_report.json").exists()
    assert count_jsonl(output_dir / "sweagent_live_single_events.jsonl") == 0


@requires_monorepo
def test_cli_audit_scans_reports_and_manifests() -> None:
    result = runner.invoke(app, ["audit", str(MODELS)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report_count"] > 0
    assert payload["manifest_count"] > 0
    assert payload["hash_checked"] > 0
    assert payload["hash_missing_count"] == 0
    assert payload["hash_mismatch_count"] == 0
    assert payload["hash_consistency_passed"] is True
