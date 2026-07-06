from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1_batch_outcomes import (
    write_protocol_v1_batch_outcomes_evidence,
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


def _write_protocol_v1_no_uplift_pair(root: Path) -> None:
    task_id = "matplotlib__matplotlib-25079"
    pair_id = "phase312_pair_018_failure_target_error_observation_recovery"
    live_dir = root / "protocol_v1_fresh_live_pair" / task_id
    official_dir = root / "protocol_v1_fresh_official_eval" / task_id
    _write_jsonl(
        live_dir / "protocol_v1_live_pair_summary.jsonl",
        [
            {
                "pair_id": pair_id,
                "source_task_id": task_id,
                "control_patch_archive_sha256": "control-sha",
                "intervention_patch_archive_sha256": "treatment-sha",
                "effect_label": "pending_or_incomplete",
                "outcome_source": "not_provided",
                "replay_prefix_output_hashes_match": True,
                "treatment_hook_event_count": 46,
                "state_capsule_equivalence_claimed": False,
            }
        ],
    )
    _write_json(
        official_dir / "protocol_v1_official_eval_report.json",
        {
            "decision": "protocol_v1_official_eval_outcome_label_ready",
            "passed": True,
            "pair_dir": live_dir.as_posix(),
            "official_eval_completed": True,
        },
    )
    _write_json(
        official_dir / "protocol_v1_dual_scorecard.json",
        {
            "pair_id": pair_id,
            "source_task_id": task_id,
            "control_resolved": False,
            "treatment_resolved": False,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "state_capsule_equivalence_claimed": False,
            "behavior_control_type": "protocol_v1_constraint_hook",
        },
    )


def _write_v0_positive_reference_pair(root: Path) -> None:
    _write_json(
        root / "sympy__sympy-21627" / "phase6_dual_scorecard.json",
        {
            "pair_id": "phase312_pair_001_failure_target_insert_validation_checkpoint",
            "source_task_id": "sympy__sympy-21627",
            "control_resolved": False,
            "treatment_resolved": True,
            "effect_label": "intervention_only_resolved_trigger_hit_candidate",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "intervention_injected_once": True,
            "state_capsule_equivalent": True,
        },
    )


def test_protocol_v1_batch_outcomes_stratifies_v1_from_v0_reference(tmp_path: Path) -> None:
    root = tmp_path / "phase6"
    _write_protocol_v1_no_uplift_pair(root)
    _write_v0_positive_reference_pair(root)

    result = write_protocol_v1_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "outcomes",
        include_v0_reference=True,
        target_protocol_v1_pair_count=4,
    )

    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_batch_outcomes_underpowered_no_uplift_observed"
    assert summary["protocol_v1_pair_count"] == 1
    assert summary["protocol_v1_positive_count"] == 0
    assert summary["protocol_v1_no_uplift_count"] == 1
    assert summary["protocol_v1_trajectory_outcome_counts"] == {
        "trajectory_diverged_no_uplift": 1
    }
    assert summary["v0_reference_pair_count"] == 1
    assert summary["v0_reference_label_counts"] == {
        "intervention_only_resolved_trigger_hit_candidate": 1
    }
    assert policy["allow_more_protocol_v1_pairs"] is True
    assert policy["allow_protocol_v2_prescription_design"] is True
    assert count_jsonl(result["pairs_path"]) == 1
    assert count_jsonl(result["reference_path"]) == 1
    assert result["manifest"]["passed"] is True


def test_cli_protocol_v1_batch_outcomes_writes_report(tmp_path: Path) -> None:
    root = tmp_path / "phase6"
    _write_protocol_v1_no_uplift_pair(root)
    output_dir = tmp_path / "cli-outcomes"

    result = runner.invoke(
        app,
        [
            "protocol-v1-batch-outcomes",
            str(root),
            "-o",
            str(output_dir),
            "--no-v0-reference",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "protocol_v1_batch_outcomes_underpowered_no_uplift_observed"
    assert payload["protocol_v1_pair_count"] == 1
    assert payload["v0_reference_pair_count"] == 0
    assert (output_dir / "protocol_v1_batch_outcomes_report.json").exists()
