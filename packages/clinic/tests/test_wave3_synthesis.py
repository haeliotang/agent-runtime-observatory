from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.wave3_synthesis import (
    CLAIM_BOUNDARY,
    build_wave3_synthesis,
    collect_wave3_evidence,
    write_wave3_synthesis_evidence,
)

runner = CliRunner()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _make_evidence_root(root: Path) -> None:
    _write_json(
        root / "protocol_v2_batch_outcomes_wave3" / "protocol_v2_batch_outcomes_report.json",
        {
            "decision": "protocol_v2_batch_outcomes_no_uplift_needs_prescription_revision",
            "summary": {
                "strict_fresh_pair_count": 4,
                "strict_fresh_source_task_ids": [
                    "pallets__flask-4045",
                    "sphinx-doc__sphinx-8474",
                ],
                "total_v2_pair_count": 5,
                "v1_reference_pair_count": 1,
                "v0_reference_pair_count": 4,
            },
            "continuation_policy": {"allow_continue_remaining_fresh_targets": False},
        },
    )
    _write_json(
        root / "protocol_v2_epsilon_estimate" / "pooled" / "epsilon_report.json",
        {
            "decision": "epsilon_noise_floor_estimated",
            "pooled_estimate": {
                "point_estimate": 0.0,
                "rerun_count": 6,
                "flip_count": 0,
                "wilson_upper_95": 0.39,
            },
            "per_instance_estimates": {
                "pallets__flask-4045": {"rerun_count": 3, "flip_count": 0},
                "sphinx-doc__sphinx-8474": {"rerun_count": 3, "flip_count": 0},
            },
        },
    )
    _write_json(
        root / "protocol_v2_mechanistic_endpoints" / "mechanistic_endpoints_report.json",
        {"decision": "mechanistic_endpoints_ready_divergence_without_outcome_change"},
    )
    mech_rows = [
        {
            "source_task_id": "sphinx-doc__sphinx-8474",
            "control": {"gold_edit_distance": 0.10},
            "treatment": {"gold_edit_distance": 1.0},
            "arm_divergence": {"first_divergence_step": 32},
        },
        {
            "source_task_id": "sphinx-doc__sphinx-8435",
            "control": {"gold_edit_distance": 0.68},
            "treatment": {"gold_edit_distance": 0.94},
            "arm_divergence": {"first_divergence_step": 33},
        },
    ]
    pairs_path = root / "protocol_v2_mechanistic_endpoints" / "mechanistic_endpoints_pairs.jsonl"
    pairs_path.write_text("".join(json.dumps(row) + "\n" for row in mech_rows), encoding="utf-8")
    _write_json(
        root
        / "protocol_v2_oracle_probe"
        / "sphinx-doc__sphinx-8474"
        / "outcome"
        / "oracle_probe_outcome_report.json",
        {
            "source_task_id": "sphinx-doc__sphinx-8474",
            "decision": "oracle_probe_outcome_unmoved_channel_bottleneck_implicated",
            "three_arm_outcomes": {"oracle_treatment_resolved": False},
        },
    )
    _write_json(
        root
        / "protocol_v2_oracle_probe"
        / "sphinx-doc__sphinx-8474"
        / "replay_free"
        / "outcome"
        / "oracle_probe_outcome_report.json",
        {
            "source_task_id": "sphinx-doc__sphinx-8474",
            "decision": "oracle_probe_replay_free_unmoved_channel_capacity_implicated",
            "variant": "replay_free",
            "three_arm_outcomes": {"oracle_treatment_resolved": False},
            "proximity": {
                "gold_file_overlap": {"jaccard": 1.0, "hit_any_gold_file": True},
                "gold_edit_distance": 0.82,
                "near_gold": False,
            },
        },
    )
    _write_json(
        root
        / "protocol_v2_oracle_probe"
        / "sphinx-doc__sphinx-8474"
        / "dose_verbatim"
        / "outcome"
        / "oracle_probe_outcome_report.json",
        {
            "source_task_id": "sphinx-doc__sphinx-8474",
            "decision": "oracle_probe_replay_free_unmoved_capability_ceiling_implicated",
            "variant": "dose_verbatim",
            "three_arm_outcomes": {"oracle_treatment_resolved": False},
            "proximity": {
                "gold_file_overlap": {"jaccard": 1.0, "hit_any_gold_file": True},
                "gold_edit_distance": 0.097,
                "near_gold": True,
            },
        },
    )
    _write_json(
        root / "instance_validity" / "instance_validity_report.json",
        {
            "decision": "instance_validity_substrate_invalid_instances_found",
            "rows": [
                {"instance_id": "pallets__flask-4045", "substrate_valid": True},
                {"instance_id": "sphinx-doc__sphinx-8474", "substrate_valid": False},
            ],
            "valid_instances": ["pallets__flask-4045"],
            "invalid_instances": ["sphinx-doc__sphinx-8474"],
        },
    )


def test_collect_finds_all_lines(tmp_path: Path) -> None:
    _make_evidence_root(tmp_path)
    evidence = collect_wave3_evidence(tmp_path)
    assert evidence["batch"] is not None
    assert evidence["epsilon"] is not None
    assert evidence["mechanistic"] is not None
    assert evidence["validity"] is not None
    assert len(evidence["oracle_rows"]) == 3
    assert len(evidence["replay_free_reports"]) == 1
    assert "dose_verbatim" in evidence["probe_reports_by_variant"]


def test_build_synthesis_numbers_match_sources(tmp_path: Path) -> None:
    _make_evidence_root(tmp_path)
    synthesis = build_wave3_synthesis(collect_wave3_evidence(tmp_path))
    values = synthesis["values"]
    # Numbers must come from the artifacts, not transcription.
    assert values["total_pairs"] == 10  # 5 + 1 + 4
    assert values["invalid_count"] == 1
    assert values["checked_count"] == 2
    # Of the two strict-fresh ids, only flask is substrate-valid.
    assert values["valid_strict_fresh"] == 1
    assert "pallets__flask-4045" in values["valid_strict_fresh_ids"]
    # Epsilon rescoped to valid instances: only flask's 3 reruns count.
    assert values["valid_epsilon_n"] == 3
    assert values["valid_epsilon_flips"] == 0
    # Dose ladder picked up from dose_verbatim + replay_free guidance.
    assert values["dose_instance"] == "sphinx-doc__sphinx-8474"
    dose = json.loads(values["dose_distances"])
    assert dose["verbatim"] == 0.097
    assert dose["guidance"] == 0.82
    # Semantic momentum picks 8474 (gap 0.82 - 0.10).
    assert values["momentum_instance"] == "sphinx-doc__sphinx-8474"
    assert abs(values["momentum_oracle_distance"] - 0.82) < 1e-9
    assert len(synthesis["findings"]) == 6
    # "uplift" may only appear in negated form ("no uplift"/"no-uplift"); never as a positive claim.
    for finding in synthesis["findings"]:
        statement = finding["statement"].lower()
        negated = statement.count("no uplift") + statement.count("no-uplift")
        assert statement.count("uplift") == negated


def test_write_synthesis_validity_supersedes(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_root(root)
    out = tmp_path / "out"
    result = write_wave3_synthesis_evidence(root, out)
    report = result["report"]
    assert report["decision"] == "wave3_synthesis_substrate_validity_supersedes_outcome_findings"
    assert report["passed"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert (out / "wave3_synthesis_report.json").is_file()
    assert (out / "wave3_synthesis.md").is_file()
    assert (out / "wave3_synthesis_manifest.json").is_file()
    md = (out / "wave3_synthesis.md").read_text()
    assert "Next-step gates" in md
    assert "contaminated" in md  # claim boundary present in narrative
    assert "substrate_revalidation" in md


def test_write_synthesis_all_valid_keeps_original_decision(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_root(root)
    _write_json(
        root / "instance_validity" / "instance_validity_report.json",
        {
            "decision": "instance_validity_all_checked_instances_valid",
            "rows": [{"instance_id": "pallets__flask-4045", "substrate_valid": True}],
            "valid_instances": ["pallets__flask-4045"],
            "invalid_instances": [],
        },
    )
    result = write_wave3_synthesis_evidence(root, tmp_path / "out")
    assert (
        result["report"]["decision"] == "wave3_synthesis_bottleneck_localized_last_mile_semantics"
    )


def test_write_synthesis_blocked_when_missing_line(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_root(root)
    # Remove the epsilon line.
    (root / "protocol_v2_epsilon_estimate" / "pooled" / "epsilon_report.json").unlink()
    result = write_wave3_synthesis_evidence(root, tmp_path / "out")
    assert result["report"]["decision"] == "wave3_synthesis_blocked_missing_evidence_line"
    assert result["report"]["passed"] is False


def test_write_synthesis_blocked_when_missing_validity(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_root(root)
    (root / "instance_validity" / "instance_validity_report.json").unlink()
    result = write_wave3_synthesis_evidence(root, tmp_path / "out")
    assert result["report"]["decision"] == "wave3_synthesis_blocked_missing_evidence_line"


def test_cli_wave3_synthesis(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_root(root)
    out = tmp_path / "out"
    result = runner.invoke(app, ["wave3-synthesis", str(root), "-o", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "wave3_synthesis_substrate_validity_supersedes_outcome_findings"
    assert payload["finding_count"] == 6
