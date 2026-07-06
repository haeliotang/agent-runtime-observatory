from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.intervention.paired_fork import run_paired_fork_dry_run
from wutai_clinic.io import count_jsonl


def test_paired_fork_dry_run_writes_ready_package_without_official_eval(tmp_path: Path) -> None:
    result = run_paired_fork_dry_run(output_dir=tmp_path)
    report = result["report"]
    manifest = result["manifest"]

    assert report["passed"] is True
    assert report["decision"] == "paired_fork_dry_run_ready_no_official_eval"
    assert report["effect_label"] == "pending_or_incomplete"
    assert report["gates"]["external_provider_not_called"] is True
    assert report["gates"]["docker_not_started"] is True
    assert report["gates"]["official_eval_not_claimed"] is True
    assert report["mock_outcome_used"] is False
    assert count_jsonl(result["events_path"]) == 6
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 5
    for path_key in [
        "protocol_path",
        "control_capsule_path",
        "treatment_capsule_path",
        "events_path",
        "report_path",
        "manifest_path",
    ]:
        assert result[path_key].exists()


def test_paired_fork_dry_run_can_emit_mock_positive_label_without_uplift_claim(
    tmp_path: Path,
) -> None:
    result = run_paired_fork_dry_run(
        output_dir=tmp_path,
        control_resolved=False,
        treatment_resolved=True,
    )
    report = json.loads(Path(result["report_path"]).read_text())

    assert report["passed"] is True
    assert report["decision"] == "paired_fork_dry_run_mock_outcome_label_ready"
    assert report["effect_label"] == "intervention_only_resolved_trigger_hit_candidate"
    assert report["mock_outcome_used"] is True
    assert report["gates"]["generalized_uplift_claim_not_made"] is True
    assert "does not start Docker" in report["claim_boundary"]


def test_paired_fork_dry_run_blocks_capsule_mismatch(tmp_path: Path) -> None:
    result = run_paired_fork_dry_run(
        output_dir=tmp_path,
        treatment_capsule_overrides={"model_request_hash": "different"},
    )
    report = result["report"]

    assert report["passed"] is False
    assert report["decision"] == "paired_fork_dry_run_blocked"
    assert report["effect_label"] == "state_mismatch_no_attribution"
    assert report["gates"]["fork_equivalence_passed"] is False
    assert report["fork_equivalence"]["mismatched_fields"] == ["model_request_hash"]
