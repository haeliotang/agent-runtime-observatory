from __future__ import annotations

import pytest

from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    paired_replay_effect_label,
    protocol_check_report,
    simulate_protocol,
    verify_fork_equivalence,
)


def _capsule(**overrides: str) -> StateCapsule:
    data = {
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
    data.update(overrides)
    return StateCapsule.from_dict(data)


def _protocol() -> InterventionProtocol:
    return InterventionProtocol.from_dict(
        {
            "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
            "action": {
                "type": "inject_system_prompt",
                "message_id": "break_recurrence_and_replan",
            },
            "guard": {"debounce": "once_per_pair", "raw_payload_logging": False},
            "claim": {"allowed": "bounded_next_step_control"},
        }
    )


def test_state_capsule_fingerprint_blocks_mismatched_model_request() -> None:
    fork = verify_fork_equivalence(
        _capsule(),
        _capsule(model_request_hash="different-model-request"),
    )

    assert fork["passed"] is False
    assert fork["decision"] == "state_mismatch_no_attribution"
    assert fork["mismatched_fields"] == ["model_request_hash"]
    assert (
        paired_replay_effect_label(
            fork_equivalence=fork,
            trigger_hit=True,
            injection_count=1,
            control_resolved=False,
            treatment_resolved=True,
        )
        == "state_mismatch_no_attribution"
    )


def test_protocol_v0_rejects_executable_and_raw_payload_fields() -> None:
    with pytest.raises(ValueError, match="forbids executable fields"):
        InterventionProtocol.from_dict(
            {
                "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
                "action": {
                    "type": "inject_system_prompt",
                    "message_id": "break_recurrence_and_replan",
                    "python": "lambda state: state",
                },
                "guard": {"debounce": "once_per_pair", "raw_payload_logging": False},
                "claim": {"allowed": "bounded_next_step_control"},
            }
        )
    with pytest.raises(ValueError, match="raw_payload_logging=false"):
        InterventionProtocol.from_dict(
            {
                "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
                "action": {
                    "type": "inject_system_prompt",
                    "message_id": "break_recurrence_and_replan",
                },
                "guard": {"debounce": "once_per_pair", "raw_payload_logging": True},
                "claim": {"allowed": "bounded_next_step_control"},
            }
        )


def test_protocol_v0_live_trigger_debounces_to_one_injection() -> None:
    simulation = simulate_protocol(
        _protocol(),
        [{"error_streak": 0}, {"error_streak": 3}, {"error_streak": 4}],
    )

    assert simulation["trigger_hit"] is True
    assert simulation["injection_count"] == 1
    assert [event["injected"] for event in simulation["events"]] == [False, True, False]
    assert all(event["raw_payload_logged"] is False for event in simulation["events"])


def test_protocol_v0_defaults_guard_to_no_raw_payload_logging() -> None:
    protocol = InterventionProtocol.from_dict(
        {
            "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
            "action": {
                "type": "inject_system_prompt",
                "message_id": "break_recurrence_and_replan",
            },
            "claim": {"allowed": "bounded_next_step_control"},
        }
    )

    assert protocol.guard.debounce == "once_per_pair"
    assert protocol.guard.raw_payload_logging is False


def test_conditional_attribution_only_allows_positive_candidate_when_all_gates_hold() -> None:
    fork = verify_fork_equivalence(_capsule(), _capsule())

    assert (
        paired_replay_effect_label(
            fork_equivalence=fork,
            trigger_hit=True,
            injection_count=1,
            control_resolved=False,
            treatment_resolved=True,
        )
        == "intervention_only_resolved_trigger_hit_candidate"
    )
    assert (
        paired_replay_effect_label(
            fork_equivalence=fork,
            trigger_hit=False,
            injection_count=0,
            control_resolved=False,
            treatment_resolved=True,
        )
        == "secondary_audit_no_treatment_attribution"
    )
    assert (
        paired_replay_effect_label(
            fork_equivalence=fork,
            trigger_hit=True,
            injection_count=2,
            control_resolved=False,
            treatment_resolved=True,
        )
        == "invalid_injection_count_no_attribution"
    )


def test_protocol_check_report_preserves_claim_boundary() -> None:
    report = protocol_check_report(
        protocol=_protocol(),
        control_capsule=_capsule(),
        treatment_capsule=_capsule(),
        feature_windows=[{"error_streak": 3}],
        control_resolved=False,
        treatment_resolved=True,
    )

    assert report["passed"] is True
    assert report["decision"] == "intervention_only_resolved_trigger_hit_candidate"
    assert report["gates"]["generalized_uplift_claim_not_made"] is True
    assert "generalized causal-uplift" in report["claim_boundary"]
