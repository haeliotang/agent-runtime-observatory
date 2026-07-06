from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.intervention.attribution import (
    attribute_pair_summaries,
    classify_pair_summary,
    continuation_policy,
    outcome_summary,
)
from wutai_clinic.io import read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_phase316_batch01_outcome_summary_matches_frozen_limited_attribution_report() -> None:
    pair_summary = list(
        read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl")
    )
    expected_report = json.loads(
        (MODELS / "phase316_batch01_limited_attribution_report.json").read_text()
    )

    assert outcome_summary(pair_summary) == expected_report["outcome_summary"]


def test_phase316_batch01_continuation_policy_matches_frozen_report() -> None:
    pair_summary = list(
        read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl")
    )
    expected_report = json.loads(
        (MODELS / "phase316_batch01_limited_attribution_report.json").read_text()
    )
    summary = outcome_summary(pair_summary)

    assert continuation_policy(summary) == expected_report["continuation_policy"]


def test_phase316_pair_classification_and_rates_match_batch01_evidence() -> None:
    pair_summary = list(
        read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl")
    )

    assert [classify_pair_summary(row) for row in pair_summary] == [
        "main_treatment",
        "trigger_miss",
        "main_treatment",
        "trigger_miss",
    ]
    attribution = attribute_pair_summaries(pair_summary)
    assert attribution["classification_counts"] == {
        "main_treatment": 2,
        "trigger_miss": 2,
        "invalid": 0,
    }
    assert attribution["trigger_hit_rate"] == 0.5
    assert attribution["intervention_success_rate"] == 0.5
    assert attribution["resolved_delta"] == 1
