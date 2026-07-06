from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.engine.diagnoser import (
    canonical_step,
    diagnose,
    diagnose_from_features,
    group_features,
)
from wutai_clinic.io import read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
MODELS = OBSERVATORY_ROOT / "models"
REPO_ROOT = OBSERVATORY_ROOT.parent


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_diagnose_from_features_matches_phase311_candidates_first_10() -> None:
    chain_rows = {
        row["trajectory_id"]: row
        for row in read_jsonl(MODELS / "phase310_swe_lite300_evidence_chain.jsonl")
    }
    features_by_id = group_features(list(read_jsonl(MODELS / "phase310_str_prefix_features.jsonl")))
    candidate_rows = list(read_jsonl(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"))[
        :10
    ]

    for expected in candidate_rows:
        trajectory_id = expected["trajectory_id"]
        chain_row = chain_rows[trajectory_id]
        trajectory = _load_json(REPO_ROOT / chain_row["trajectory_path"])
        states = [canonical_step(step) for step in trajectory.get("trajectory") or []]
        actual = diagnose_from_features(
            chain_row=chain_row,
            feature_rows=features_by_id[trajectory_id],
            canonical_states=states,
        )
        expected_without_outcome = {
            key: value for key, value in expected.items() if key != "outcome_context_for_audit_only"
        }
        assert actual == expected_without_outcome


def test_diagnose_accepts_prefix_features() -> None:
    expected = next(read_jsonl(MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"))
    chain_rows = {
        row["trajectory_id"]: row
        for row in read_jsonl(MODELS / "phase310_swe_lite300_evidence_chain.jsonl")
    }
    features_by_id = group_features(list(read_jsonl(MODELS / "phase310_str_prefix_features.jsonl")))
    trajectory_id = expected["trajectory_id"]
    chain_row = chain_rows[trajectory_id]
    trajectory = _load_json(REPO_ROOT / chain_row["trajectory_path"])

    actual = diagnose(
        trajectory,
        features_by_id[trajectory_id],
        chain_row=chain_row,
    )

    expected_without_outcome = {
        key: value for key, value in expected.items() if key != "outcome_context_for_audit_only"
    }
    assert actual == expected_without_outcome
