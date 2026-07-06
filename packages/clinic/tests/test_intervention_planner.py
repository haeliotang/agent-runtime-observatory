from __future__ import annotations

from collections import Counter
from pathlib import Path

from wutai_clinic.intervention.planner import (
    arm_rows,
    build_package_rows,
    build_pairs,
    candidate_records,
)
from wutai_clinic.io import read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
PACKAGE_DIR = MODELS / "phase312_paired_intervention_package"


def test_phase312_candidate_records_and_package_rows_match_frozen_package() -> None:
    candidates = list(read_jsonl(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"))
    expected_pairs = list(read_jsonl(PACKAGE_DIR / "pairs.jsonl"))
    expected_arms = list(read_jsonl(PACKAGE_DIR / "arms.jsonl"))

    records = candidate_records(candidates)
    pairs = build_pairs(records)
    arms = arm_rows(pairs)

    assert pairs == expected_pairs
    assert arms == expected_arms


def test_build_package_rows_summary_matches_phase312_report() -> None:
    candidates = list(read_jsonl(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"))
    pairs, arms = build_package_rows(candidates)
    policy_counts = Counter(pair["intervention_policy_id"] for pair in pairs)
    role_counts = Counter(pair["selection_role"] for pair in pairs)
    family_counts = Counter(pair["source_family"] for pair in pairs)
    arm_counts = Counter(arm["arm_type"] for arm in arms)

    assert len(pairs) == 32
    assert len(arms) == 64
    assert dict(sorted(policy_counts.items())) == {
        "break_recurrence_and_replan": 8,
        "error_observation_recovery": 8,
        "insert_validation_checkpoint": 8,
        "same_action_escape": 8,
    }
    assert dict(sorted(role_counts.items())) == {"failure_target": 24, "success_sentinel": 8}
    assert len(family_counts) >= 6
    assert dict(sorted(arm_counts.items())) == {"control": 32, "intervention": 32}
    assert all(count == 1 for count in Counter(pair["source_task_id"] for pair in pairs).values())
