"""Tests for the schema deltas ported from wutai / stillmirror-review
(see docs/object-model-alignment.md)."""

import pytest
from aro_schema import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Coverage,
    Decision,
    GoalEvent,
    GoalEventKind,
    PolicyDecision,
    RunVerdict,
    digest_text,
)


def _decision(step: int, decision: Decision) -> PolicyDecision:
    return PolicyDecision(
        id=f"pd-{step}",
        run_id="r1",
        step_index=step,
        policy_id="p1",
        rule_id="rule",
        decision=decision,
        reason="test",
    )


def test_verdict_trusted_when_no_gated_decisions():
    run = AgentRun(id="r1", task_id="t1", agent="scripted@0.1")
    assert run.verdict == RunVerdict.TRUSTED


def test_verdict_rolls_up_like_wutai():
    review = AgentRun(
        id="r1",
        task_id="t1",
        agent="a",
        policy_decisions=[_decision(0, Decision.NEEDS_REVIEW)],
    )
    assert review.verdict == RunVerdict.REVIEW_REQUIRED
    blocked = AgentRun(
        id="r1",
        task_id="t1",
        agent="a",
        policy_decisions=[_decision(0, Decision.NEEDS_REVIEW), _decision(1, Decision.DENY)],
    )
    assert blocked.verdict == RunVerdict.BLOCKED


def test_verdict_serializes_into_run_json():
    run = AgentRun(id="r1", task_id="t1", agent="a")
    assert '"verdict":"trusted"' in run.model_dump_json()
    # and round-trips: the computed field is ignored on input
    assert AgentRun.model_validate_json(run.model_dump_json()).id == "r1"


def test_attestation_requires_named_human():
    with pytest.raises(ValueError):
        Attestation(
            id="att-1",
            run_id="r1",
            decision=AttestationDecision.ACCEPT,
            declared_scope="the artifacts",
            subject_digest=digest_text("x"),
        )  # no attested_by


def test_attestation_scoped_ratification_roundtrip():
    attestation = Attestation(
        id="att-1",
        run_id="r1",
        decision=AttestationDecision.ACCEPT,
        declared_scope="I ratify the recorded artifacts and policy decisions.",
        excluded_scope="I do not ratify trace completeness or external side effects.",
        proposed_by="assistant",
        attested_by="Hao",
        subject_digest=digest_text("run-json"),
    )
    restored = Attestation.model_validate_json(attestation.model_dump_json())
    assert restored == attestation
    assert restored.proposed_by != restored.attested_by  # draft is not attestation


def test_goal_event_lifecycle_vocabulary():
    event = GoalEvent(
        id="ge-1", goal_id="g1", kind=GoalEventKind.REPLACED, replaced_by_goal_id="g2"
    )
    assert event.kind == "replaced"
    assert set(GoalEventKind) == {"introduced", "reinforced", "replaced", "retired"}


def test_coverage_on_run_roundtrip():
    run = AgentRun(
        id="r1",
        task_id="t1",
        agent="a",
        coverage=Coverage(captured=["steps"], blind_spots=["network"], enforcement=["policy"]),
    )
    restored = AgentRun.model_validate_json(run.model_dump_json())
    assert restored.coverage == run.coverage
