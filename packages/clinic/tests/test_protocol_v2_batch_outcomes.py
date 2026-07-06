from __future__ import annotations

import json
from pathlib import Path


from wutai_clinic.intervention.protocol_v2_batch_outcomes import (
    write_protocol_v2_batch_outcomes_evidence,
)
from wutai_clinic.io import count_jsonl


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_v2_no_uplift_scorecard(
    root: Path,
    task_id: str = "sphinx-doc__sphinx-8474",
    pair_id: str = "phase312_pair_015_failure_target_error_observation_recovery",
) -> None:
    """Write a v2 dual scorecard with the no-uplift label."""
    eval_dir = root / "protocol_v2_official_eval" / task_id
    _write_json(
        eval_dir / "protocol_v2_dual_scorecard.json",
        {
            "behavior_control_type": "protocol_v2_constraint_hook",
            "control_resolved": False,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "pair_id": pair_id,
            "single_pair_only": True,
            "source_task_id": task_id,
            "state_capsule_equivalence_claimed": False,
            "treatment_resolved": False,
        },
    )
    _write_json(
        eval_dir / "protocol_v2_official_eval_report.json",
        {
            "decision": "protocol_v2_official_eval_outcome_label_ready",
            "passed": True,
            "official_eval_completed": True,
            "pair_id": pair_id,
            "source_task_id": task_id,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
        },
    )


def _write_v2_uplift_scorecard(
    root: Path,
    task_id: str = "django__django-99999",
    pair_id: str = "phase312_pair_099_failure_target_uplift",
) -> None:
    """Write a v2 dual scorecard with a positive (uplift) label."""
    eval_dir = root / "protocol_v2_official_eval" / task_id
    _write_json(
        eval_dir / "protocol_v2_dual_scorecard.json",
        {
            "behavior_control_type": "protocol_v2_constraint_hook",
            "control_resolved": False,
            "effect_label": "intervention_only_resolved_trigger_hit_candidate",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "pair_id": pair_id,
            "single_pair_only": True,
            "source_task_id": task_id,
            "state_capsule_equivalence_claimed": False,
            "treatment_resolved": True,
        },
    )


def _write_v2_reference_scorecard(
    root: Path,
    task_id: str = "sympy__sympy-16281",
    pair_id: str = "phase312_pair_010_failure_target_break_recurrence_and_replan",
) -> None:
    """Write a v2 dual scorecard that is NOT in the fresh gate list (reference)."""
    eval_dir = root / "protocol_v2_official_eval" / task_id
    _write_json(
        eval_dir / "protocol_v2_dual_scorecard.json",
        {
            "behavior_control_type": "protocol_v2_constraint_hook",
            "control_resolved": False,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "pair_id": pair_id,
            "single_pair_only": True,
            "source_task_id": task_id,
            "state_capsule_equivalence_claimed": False,
            "treatment_resolved": False,
        },
    )


def _write_fresh_candidate_list(
    root: Path,
    task_ids: list[str],
) -> None:
    """Write a protocol_v2_fresh_candidate_set_candidates.jsonl under root."""
    gate_dir = root / "protocol_v2_fresh_candidate_gate"
    _write_jsonl(
        gate_dir / "protocol_v2_fresh_candidate_set_candidates.jsonl",
        [{"source_task_id": tid, "fresh_rank": i + 1} for i, tid in enumerate(task_ids)],
    )


def _write_v1_reference_scorecard(root: Path) -> None:
    task_id = "matplotlib__matplotlib-25079"
    pair_id = "phase312_pair_018_v1_ref"
    eval_dir = root / "protocol_v1_fresh_official_eval" / task_id
    _write_json(
        eval_dir / "protocol_v1_dual_scorecard.json",
        {
            "behavior_control_type": "protocol_v1_constraint_hook",
            "control_resolved": False,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
            "outcome_source": "official_eval",
            "pair_id": pair_id,
            "source_task_id": task_id,
            "state_capsule_equivalence_claimed": False,
            "treatment_resolved": False,
        },
    )


def _write_v0_reference_scorecard(root: Path) -> None:
    _write_json(
        root / "sympy__sympy-21627" / "phase6_dual_scorecard.json",
        {
            "pair_id": "phase312_pair_001_v0_ref",
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stratification_strict_fresh_vs_reference(tmp_path: Path) -> None:
    """Task IDs in the fresh gate list → v2_strict_fresh; others → v2_reference."""
    root = tmp_path / "ev"
    fresh_task = "sphinx-doc__sphinx-8474"
    ref_task = "sympy__sympy-16281"

    _write_v2_no_uplift_scorecard(root, task_id=fresh_task)
    _write_v2_reference_scorecard(root, task_id=ref_task)
    _write_fresh_candidate_list(root, [fresh_task])  # only fresh_task is fresh

    result = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out",
        include_v1_reference=False,
        include_v0_reference=False,
        target_pair_count=4,
    )

    summary = result["report"]["summary"]
    assert summary["strict_fresh_pair_count"] == 1
    assert summary["reference_pair_count"] == 1
    assert summary["v1_reference_pair_count"] == 0
    assert summary["v0_reference_pair_count"] == 0
    # no uplift in either stratum
    assert summary["uplift_pair_count"] == 0
    assert summary["harm_pair_count"] == 0
    # fresh list was present
    assert summary["fresh_list_degraded"] is False

    # strict-fresh JSONL has exactly 1 row
    assert count_jsonl(result["pairs_path"]) == 1
    # reference JSONL has exactly 1 row
    assert count_jsonl(result["ref_v2_path"]) == 1


def test_fallback_when_fresh_list_missing(tmp_path: Path) -> None:
    """When the fresh candidate list is absent, all v2 pairs go to reference stratum
    and fresh_list_degraded is flagged True."""
    root = tmp_path / "ev"
    _write_v2_no_uplift_scorecard(root)
    # intentionally do NOT write any fresh candidate list

    result = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out",
        include_v1_reference=False,
        include_v0_reference=False,
        target_pair_count=4,
    )

    summary = result["report"]["summary"]
    # with no fresh list, no task_id can match → all go to reference
    assert summary["strict_fresh_pair_count"] == 0
    assert summary["reference_pair_count"] == 1
    assert summary["fresh_list_degraded"] is True

    gates = result["report"]["gates"]
    assert gates["fresh_list_present"] is False
    # but decision should NOT be blocked (fresh_list_present is advisory)
    assert result["report"]["decision"] != "protocol_v2_batch_outcomes_blocked"


def test_one_uplift_pair_changes_label_counts_and_decision(tmp_path: Path) -> None:
    """A single uplift pair in the fresh stratum changes uplift_pair_count and decision."""
    root = tmp_path / "ev"
    fresh_task_uplift = "django__django-99999"
    # put it in the fresh list
    _write_fresh_candidate_list(root, [fresh_task_uplift])
    _write_v2_uplift_scorecard(root, task_id=fresh_task_uplift)

    result = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out",
        include_v1_reference=False,
        include_v0_reference=False,
        target_pair_count=4,  # target not met (1 < 4), but uplift present
    )

    summary = result["report"]["summary"]
    assert summary["strict_fresh_pair_count"] == 1
    assert summary["uplift_pair_count"] == 1
    assert summary["harm_pair_count"] == 0
    assert summary["strict_fresh_uplift_count"] == 1
    # decision: underpowered but NOT pure no-uplift
    assert result["report"]["decision"] == "protocol_v2_batch_outcomes_underpowered_continue_sampling"


def test_v1_v0_context_switches(tmp_path: Path) -> None:
    """--no-v1-reference and --no-v0-reference suppress those strata entirely."""
    root = tmp_path / "ev"
    fresh_task = "sphinx-doc__sphinx-8474"
    _write_v2_no_uplift_scorecard(root, task_id=fresh_task)
    _write_fresh_candidate_list(root, [fresh_task])
    _write_v1_reference_scorecard(root)
    _write_v0_reference_scorecard(root)

    # with both references ON
    r_both = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out_both",
        include_v1_reference=True,
        include_v0_reference=True,
        target_pair_count=4,
    )
    assert r_both["report"]["summary"]["v1_reference_pair_count"] == 1
    assert r_both["report"]["summary"]["v0_reference_pair_count"] == 1

    # with both references OFF
    r_none = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out_none",
        include_v1_reference=False,
        include_v0_reference=False,
        target_pair_count=4,
    )
    assert r_none["report"]["summary"]["v1_reference_pair_count"] == 0
    assert r_none["report"]["summary"]["v0_reference_pair_count"] == 0

    # with only v1 off
    r_no_v1 = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out_no_v1",
        include_v1_reference=False,
        include_v0_reference=True,
        target_pair_count=4,
    )
    assert r_no_v1["report"]["summary"]["v1_reference_pair_count"] == 0
    assert r_no_v1["report"]["summary"]["v0_reference_pair_count"] == 1


def test_write_protocol_v2_batch_outcomes_evidence_direct(tmp_path: Path) -> None:
    """Full integration test calling write_protocol_v2_batch_outcomes_evidence with synthetic data."""
    root = tmp_path / "ev"
    fresh_task = "sphinx-doc__sphinx-8474"
    ref_task = "sympy__sympy-16281"

    _write_v2_no_uplift_scorecard(root, task_id=fresh_task)
    _write_v2_reference_scorecard(root, task_id=ref_task)
    _write_fresh_candidate_list(root, [fresh_task])
    _write_v1_reference_scorecard(root)
    _write_v0_reference_scorecard(root)

    out = tmp_path / "out"
    result = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=out,
        include_v1_reference=True,
        include_v0_reference=True,
        target_pair_count=4,
    )

    report = result["report"]
    summary = report["summary"]

    # files must exist
    assert result["report_path"].exists()
    assert result["manifest_path"].exists()
    assert result["pairs_path"].exists()
    assert result["ref_v2_path"].exists()
    assert result["ref_v1_path"].exists()
    assert result["ref_v0_path"].exists()

    # stratification checks
    assert summary["strict_fresh_pair_count"] == 1
    assert summary["reference_pair_count"] == 1
    assert summary["v1_reference_pair_count"] == 1
    assert summary["v0_reference_pair_count"] == 1

    # counts must not be mixed across layers
    assert summary["uplift_pair_count"] == 0  # v2 strata only
    assert summary["harm_pair_count"] == 0

    # claim_boundary must be present
    assert report["claim_boundary"]

    # expected decision for current data (1 strict-fresh, no uplift, target not met)
    assert report["decision"] == "protocol_v2_batch_outcomes_underpowered_no_uplift_observed"
    assert report["passed"] is True

    # continuation policy: BLOCKS must be False
    policy = report["continuation_policy"]
    assert policy["allow_stability_claim"] is False
    assert policy["allow_same_pair_positive_attribution"] is False
    assert policy["allow_generalized_uplift_claim"] is False
    assert policy["allow_efe_str_predictive_claim"] is False
    assert policy["allow_full_unattended_run"] is False

    # continuation policy: ALLOWS
    assert policy["allow_continue_remaining_fresh_targets"] is True
    assert policy["allow_power_analysis_consuming_this_report"] is True

    # manifest
    assert result["manifest"]["passed"] is True
    assert len(result["manifest"]["artifacts"]) >= 5


def test_no_layer_mixing_in_counts(tmp_path: Path) -> None:
    """v0 uplift must NOT bleed into v2 uplift_pair_count."""
    root = tmp_path / "ev"
    fresh_task = "sphinx-doc__sphinx-8474"
    _write_v2_no_uplift_scorecard(root, task_id=fresh_task)
    _write_fresh_candidate_list(root, [fresh_task])
    _write_v0_reference_scorecard(root)  # v0 has an uplift — must NOT count in v2

    result = write_protocol_v2_batch_outcomes_evidence(
        root=root,
        output_dir=tmp_path / "out",
        include_v1_reference=False,
        include_v0_reference=True,
        target_pair_count=4,
    )
    summary = result["report"]["summary"]
    # v2 uplift must be 0 even though v0 reference has 1 uplift
    assert summary["uplift_pair_count"] == 0
    assert summary["v0_reference_pair_count"] == 1
    # v0 label shows uplift in its own counter
    assert "intervention_only_resolved_trigger_hit_candidate" in summary["v0_reference_label_counts"]
