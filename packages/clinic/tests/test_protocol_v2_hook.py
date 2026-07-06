from __future__ import annotations

import pytest

from wutai_clinic.intervention.protocol_v2 import protocol_v2_prescription_template
from wutai_clinic.intervention.protocol_v2_hook import (
    ProtocolV2ConstraintHook,
    ProtocolV2ConstraintViolation,
)


def _hook() -> ProtocolV2ConstraintHook:
    return ProtocolV2ConstraintHook(
        protocol=protocol_v2_prescription_template(),
        source_task_id="sympy__sympy-16281",
        pair_id="phase312_pair_010_failure_target_break_recurrence_and_replan",
    )


def test_protocol_v2_hook_blocks_source_edit_until_failure_materialized() -> None:
    hook = _hook()

    event = hook.before_action("str_replace_editor str_replace /testbed/sympy/core/foo.py")

    assert event["blocked"] is True
    assert event["constraint_id"] == "require_explicit_failure_reproduction"
    assert hook.blocking_event_count == 1


def test_protocol_v2_hook_requires_context_broadening_after_failure() -> None:
    hook = _hook()
    hook.replay_prefix_action_count = 1

    replay = hook.before_action("python reproduce_failure.py")
    hook.after_action("python reproduce_failure.py", "AssertionError: failed")
    edit_without_context = hook.before_action(
        "str_replace_editor str_replace /testbed/sympy/core/foo.py"
    )
    context = hook.before_action("rg 'class Foo' /testbed/sympy")
    hook.after_action("rg 'class Foo' /testbed/sympy", "sympy/core/foo.py:class Foo")
    edit_after_context = hook.before_action(
        "str_replace_editor str_replace /testbed/sympy/core/foo.py"
    )

    assert replay["event"] == "protocol_v2_replay_action_allowed"
    assert replay["blocked"] is False
    assert hook.state.failure_materialized is True
    assert edit_without_context["blocked"] is True
    assert edit_without_context["constraint_id"] == (
        "require_alternative_hypothesis_before_next_patch"
    )
    assert context["blocked"] is False
    assert hook.state.context_broadened_after_failure is True
    assert edit_after_context["blocked"] is False


def test_protocol_v2_hook_interrupts_repeated_failure_loop() -> None:
    hook = _hook()

    for _ in range(3):
        event = hook.before_action("python reproduce_failure.py")
        hook.after_action("python reproduce_failure.py", "AssertionError: failed")
        assert event["blocked"] is False
    repeated = hook.before_action("python reproduce_failure.py")
    broaden = hook.before_action("rg AssertionError /testbed")

    assert repeated["blocked"] is True
    assert repeated["constraint_id"] == "interrupt_repeated_failure_loop"
    assert broaden["blocked"] is False


def test_protocol_v2_hook_requires_targeted_recheck_before_submit() -> None:
    hook = _hook()

    hook.before_action("python reproduce_failure.py")
    hook.after_action("python reproduce_failure.py", "AssertionError: failed")
    hook.before_action("rg AssertionError /testbed")
    hook.after_action("rg AssertionError /testbed", "sympy/foo.py:AssertionError")
    hook.before_action("str_replace_editor str_replace /testbed/sympy/foo.py")
    hook.after_action("str_replace_editor str_replace /testbed/sympy/foo.py", "edited")
    submit_before_recheck = hook.before_action("submit")
    hook.before_action("python reproduce_failure.py")
    hook.after_action("python reproduce_failure.py", "1 passed")
    submit_after_recheck = hook.before_action("submit")

    assert submit_before_recheck["blocked"] is True
    assert submit_before_recheck["constraint_id"] == "require_targeted_post_patch_recheck"
    assert submit_after_recheck["blocked"] is False


def test_protocol_v2_hook_on_action_started_raises_violation_for_blocked_action() -> None:
    hook = _hook()

    with pytest.raises(ProtocolV2ConstraintViolation) as exc:
        hook.on_action_started(
            step={"action": "str_replace_editor str_replace /testbed/sympy/foo.py"}
        )

    assert exc.value.event["blocked"] is True
    assert exc.value.event["constraint_id"] == "require_explicit_failure_reproduction"



def test_protocol_v2_hook_observe_only_records_without_raising() -> None:
    hook = ProtocolV2ConstraintHook(
        protocol=protocol_v2_prescription_template(),
        source_task_id="sphinx-doc__sphinx-8474",
        observe_only=True,
    )

    # Same action that raises in enforce mode must pass in observe-only mode.
    hook.on_action_started(
        step={"action": "str_replace_editor str_replace /testbed/sphinx/foo.py"}
    )

    (event,) = hook.audit_events
    assert event["event"] == "protocol_v2_action_would_block_observe_only"
    assert event["blocked"] is False
    assert event["would_have_blocked"] is True
    assert event["constraint_id"] == "require_explicit_failure_reproduction"


def test_protocol_v2_hook_observe_only_allowed_actions_unchanged() -> None:
    hook = ProtocolV2ConstraintHook(
        protocol=protocol_v2_prescription_template(),
        observe_only=True,
    )

    event = hook.before_action("python reproduce_failure.py")

    assert event["event"] == "protocol_v2_action_allowed"
    assert event["blocked"] is False
    assert event["would_have_blocked"] is False


def test_protocol_v2_hook_enforce_mode_default_unchanged() -> None:
    hook = _hook()

    event = hook.before_action("str_replace_editor str_replace /testbed/sympy/foo.py")

    assert event["event"] == "protocol_v2_action_blocked"
    assert event["blocked"] is True
    assert event["would_have_blocked"] is False
