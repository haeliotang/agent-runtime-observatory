from __future__ import annotations

import pytest

from wutai_clinic.intervention.protocol_v1 import protocol_v1_for_no_uplift_classification
from wutai_clinic.intervention.protocol_v1_hook import (
    ProtocolV1ConstraintHook,
    ProtocolV1ConstraintViolation,
)


def _targeted_hook() -> ProtocolV1ConstraintHook:
    return ProtocolV1ConstraintHook(
        protocol=protocol_v1_for_no_uplift_classification(
            classification="behavior_diverged_but_target_failure_persisted",
            trigger_predicate="error_streak >= 1",
        ),
        source_task_id="pytest-dev__pytest-8365",
        pair_id="pair-targeted",
    )


def _regression_hook() -> ProtocolV1ConstraintHook:
    return ProtocolV1ConstraintHook(
        protocol=protocol_v1_for_no_uplift_classification(
            classification="target_fixed_but_regression_not_controlled",
            trigger_predicate="same_action_family_streak >= 3",
        ),
        source_task_id="matplotlib__matplotlib-24970",
        pair_id="pair-regression",
    )


def test_protocol_v1_hook_blocks_source_edit_until_failure_materialized() -> None:
    hook = _targeted_hook()

    event = hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")

    assert event["blocked"] is True
    assert event["constraint_id"] == "block_edit_until_failure_reproduced_or_explained"
    assert hook.blocking_event_count == 1


def test_protocol_v1_hook_allows_replay_prefix_to_materialize_failure() -> None:
    hook = _targeted_hook()
    hook.replay_prefix_action_count = 1

    replay_edit = hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")
    hook.after_action(
        "cd /testbed && python reproduce_failure.py",
        "Traceback ... AssertionError: failed",
    )
    live_edit = hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")

    assert replay_edit["event"] == "protocol_v1_replay_action_allowed"
    assert replay_edit["blocked"] is False
    assert replay_edit["replay_prefix"] is True
    assert hook.state.failure_materialized is True
    assert live_edit["blocked"] is False


def test_protocol_v1_hook_allows_edit_after_failure_reproduction_and_requires_recheck() -> None:
    hook = _targeted_hook()

    hook.before_action("cd /testbed && python reproduce_failure.py")
    hook.after_action(
        "cd /testbed && python reproduce_failure.py",
        "Traceback ... AssertionError: failed",
    )
    edit = hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")
    hook.after_action("str_replace_editor str_replace /testbed/src/pkg.py", "edited")
    submit_before_recheck = hook.before_action("submit")
    hook.before_action("cd /testbed && python reproduce_failure.py")
    hook.after_action("cd /testbed && python reproduce_failure.py", "1 passed")
    submit_after_recheck = hook.before_action("submit")

    assert edit["blocked"] is False
    assert submit_before_recheck["blocked"] is True
    assert submit_before_recheck["constraint_id"] == "require_post_patch_target_recheck"
    assert submit_after_recheck["blocked"] is False


def test_protocol_v1_hook_blocks_submit_after_guard_regression() -> None:
    hook = _regression_hook()

    hook.before_action("str_replace_editor str_replace /testbed/lib/matplotlib/colors.py")
    hook.after_action(
        "str_replace_editor str_replace /testbed/lib/matplotlib/colors.py",
        "edited",
    )
    target_missing = hook.before_action("submit")
    hook.before_action("cd /testbed && pytest target")
    hook.after_action("cd /testbed && pytest target", "1 passed")
    guard_missing = hook.before_action("submit")
    hook.before_action("cd /testbed && pytest guard regression")
    hook.after_action(
        "cd /testbed && pytest guard regression",
        "PASS_TO_FAIL: guard regression failed",
    )
    guard_regression = hook.before_action("submit")

    assert target_missing["blocked"] is True
    assert target_missing["constraint_id"] == "require_post_patch_target_recheck"
    assert guard_missing["blocked"] is True
    assert guard_missing["constraint_id"] == "require_post_patch_guard_recheck"
    assert guard_regression["blocked"] is True
    assert guard_regression["constraint_id"] == "block_submit_on_guard_regression"


def test_protocol_v1_hook_on_action_started_raises_violation_for_blocked_action() -> None:
    hook = _targeted_hook()

    with pytest.raises(ProtocolV1ConstraintViolation) as exc:
        hook.on_action_started(step={"action": "replace_file_content /testbed/pkg.py"})

    assert exc.value.event["blocked"] is True
    assert exc.value.event["constraint_id"] == "block_edit_until_failure_reproduced_or_explained"


def test_protocol_v1_hook_exposes_sweagent_lifecycle_noops() -> None:
    hook = _targeted_hook()

    hook.on_init(agent=object())
    hook.on_tools_installation_started()
    hook.on_run_start()
    hook.on_setup_attempt()
    hook.on_setup_done()
    hook.on_step_start()
    hook.on_actions_generated(step={"action": "bash true"})
    hook.on_model_query(messages=[], agent="main")
    hook.on_query_message_added(
        agent="main",
        role="user",
        content="hello",
        message_type="observation",
        thinking_blocks=[],
    )
    hook.on_step_done(step={"action": "bash true"}, info={})
    hook.on_run_done(trajectory=[], info={})

    assert hook.audit_events == []
