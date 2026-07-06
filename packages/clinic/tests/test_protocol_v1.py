from __future__ import annotations

import pytest

from wutai_clinic.intervention.protocol_v1 import (
    PRESCRIPTION_CONSTRAINTS,
    ProtocolV1,
    build_protocol_v1_plan,
    protocol_v1_for_no_uplift_classification,
)


def test_protocol_v1_builds_targeted_failure_oracle_from_no_uplift_classification() -> None:
    protocol = protocol_v1_for_no_uplift_classification(
        classification="behavior_diverged_but_target_failure_persisted",
        trigger_predicate="error_streak >= 1",
    )

    payload = protocol.to_dict()
    assert payload["version"] == "protocol_v1"
    assert payload["action"]["prescription_id"] == "targeted_failure_oracle"
    assert payload["action"]["constraint_ids"] == list(
        PRESCRIPTION_CONSTRAINTS["targeted_failure_oracle"]
    )
    assert payload["trigger"]["oracle_source"] == "prefix_observation_required"
    assert payload["guard"]["official_eval_identifiers_runtime_visible"] is False
    assert payload["guard"]["same_pair_rerun_attribution_allowed"] is False


def test_protocol_v1_builds_regression_guarded_patch_validation() -> None:
    protocol = protocol_v1_for_no_uplift_classification(
        classification="target_fixed_but_regression_not_controlled",
        trigger_predicate="same_action_family_streak >= 3",
    )

    payload = protocol.to_dict()
    assert payload["action"]["prescription_id"] == "regression_guarded_patch_validation"
    assert payload["action"]["constraint_ids"] == list(
        PRESCRIPTION_CONSTRAINTS["regression_guarded_patch_validation"]
    )


def test_protocol_v1_rejects_executable_and_posthoc_oracle_runtime_fields() -> None:
    with pytest.raises(ValueError, match="forbids executable fields"):
        ProtocolV1.from_dict(
            {
                "version": "protocol_v1",
                "trigger": {
                    "type": "live_feature",
                    "predicate": "error_streak >= 1",
                    "oracle_source": "prefix_observation_required",
                },
                "action": {
                    "type": "enforce_action_constraints",
                    "prescription_id": "targeted_failure_oracle",
                    "python": "lambda state: state",
                },
            }
        )

    with pytest.raises(ValueError, match="forbids official eval identifiers"):
        ProtocolV1.from_dict(
            {
                "version": "protocol_v1",
                "trigger": {
                    "type": "live_feature",
                    "predicate": "error_streak >= 1",
                    "oracle_source": "prefix_observation_required",
                },
                "action": {
                    "type": "enforce_action_constraints",
                    "prescription_id": "targeted_failure_oracle",
                },
                "guard": {"official_eval_identifiers_runtime_visible": True},
            }
        )

    with pytest.raises(ValueError, match="posthoc or external oracle"):
        ProtocolV1.from_dict(
            {
                "version": "protocol_v1",
                "trigger": {
                    "type": "live_feature",
                    "predicate": "error_streak >= 1",
                    "oracle_source": "official_eval_posthoc",
                },
                "action": {
                    "type": "enforce_action_constraints",
                    "prescription_id": "targeted_failure_oracle",
                },
            }
        )


def test_protocol_v1_plan_marks_official_tests_analysis_only() -> None:
    diagnosis = {
        "decision": "phase6_no_uplift_diagnosis_complete",
        "per_pair": [
            {
                "source_task_id": "pytest-dev__pytest-8365",
                "pair_id": "pair-1",
                "no_uplift_classification": "behavior_diverged_but_target_failure_persisted",
                "trigger": {"predicate": "error_streak >= 1"},
                "tests": {
                    "intervention": {
                        "FAIL_TO_PASS": {
                            "failure": ["testing/test_tmpdir.py::target"],
                            "success": [],
                        },
                        "PASS_TO_PASS": {"failure": []},
                        "PASS_TO_FAIL": {"failure": []},
                    }
                },
            },
            {
                "source_task_id": "matplotlib__matplotlib-24970",
                "pair_id": "pair-2",
                "no_uplift_classification": "target_fixed_but_regression_not_controlled",
                "trigger": {"predicate": "same_action_family_streak >= 3"},
                "tests": {
                    "intervention": {
                        "FAIL_TO_PASS": {"failure": [], "success": ["target"]},
                        "PASS_TO_PASS": {"failure": ["regression"]},
                        "PASS_TO_FAIL": {"failure": []},
                    }
                },
            },
        ],
    }

    plan = build_protocol_v1_plan(diagnosis)

    assert plan["decision"] == "protocol_v1_plan_ready_not_live_executed"
    assert plan["same_pair_positive_claim_allowed"] is False
    assert [row["protocol_v1"]["action"]["prescription_id"] for row in plan["pairs"]] == [
        "targeted_failure_oracle",
        "regression_guarded_patch_validation",
    ]
    assert plan["pairs"][0]["same_pair_rerun_attribution_eligible"] is False
    assert plan["pairs"][0]["official_eval_tests_analysis_only"]["target_failures"] == [
        "testing/test_tmpdir.py::target"
    ]
