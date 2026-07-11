"""Semantics of consumable review debt (see docs/object-model.md):
debt derives from needs_review decisions; accept/amend clears named items;
reject and stale/blank attestations clear nothing."""

import pytest
from aro_schema import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Decision,
    PolicyDecision,
    compute_review_debt,
    run_subject_digest,
)


def _decision(i: int, decision: Decision) -> PolicyDecision:
    return PolicyDecision(
        id=f"r1-pd-{i}",
        run_id="r1",
        step_index=i,
        policy_id="p1",
        rule_id="review-sensitive-read",
        decision=decision,
        reason="test",
    )


def _run(*decisions: PolicyDecision) -> AgentRun:
    return AgentRun(id="r1", task_id="t1", agent="a", policy_decisions=list(decisions))


def _attestation(
    run: AgentRun,
    decision: AttestationDecision,
    clears: list[str],
    att_id: str = "att-1",
    subject_digest: str | None = None,
):
    return Attestation(
        id=att_id,
        run_id="r1",
        decision=decision,
        declared_scope="scope",
        attested_by="Hao",
        # Clearing is digest-bound; default to the run's real digest.
        subject_digest=subject_digest or run_subject_digest(run),
        clears_decisions=clears,
    )


def test_debt_derives_only_from_needs_review():
    run = _run(
        _decision(0, Decision.DENY),
        _decision(1, Decision.NEEDS_REVIEW),
        _decision(2, Decision.ALLOW),
    )
    items = compute_review_debt(run, [])
    assert [item.decision_id for item in items] == ["r1-pd-1"]
    assert items[0].status == "open" and items[0].cleared_by is None


def test_accept_clears_named_item():
    run = _run(_decision(1, Decision.NEEDS_REVIEW), _decision(3, Decision.NEEDS_REVIEW))
    items = compute_review_debt(run, [_attestation(run, AttestationDecision.ACCEPT, ["r1-pd-1"])])
    by_id = {item.decision_id: item for item in items}
    assert by_id["r1-pd-1"].status == "cleared"
    assert by_id["r1-pd-1"].cleared_by == "att-1"
    assert by_id["r1-pd-1"].attested_by == "Hao"
    assert by_id["r1-pd-3"].status == "open"  # not named -> still open


def test_reject_never_clears():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    items = compute_review_debt(run, [_attestation(run, AttestationDecision.REJECT, ["r1-pd-1"])])
    assert items[0].status == "open"  # the seat stays visibly empty


def test_run_level_attestation_clears_nothing():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    items = compute_review_debt(run, [_attestation(run, AttestationDecision.ACCEPT, [])])
    assert items[0].status == "open"


def test_first_clearing_attestation_wins():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    first = _attestation(run, AttestationDecision.ACCEPT, ["r1-pd-1"], att_id="att-first")
    second = _attestation(run, AttestationDecision.AMEND, ["r1-pd-1"], att_id="att-second")
    items = compute_review_debt(run, [first, second])
    assert items[0].cleared_by == "att-first"


def test_digest_mismatch_does_not_clear_and_flags_stale():
    # An attestation whose subject_digest does not match the current run cannot
    # clear its debt — the run was overwritten after review (finding 2).
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    stale = _attestation(
        run, AttestationDecision.ACCEPT, ["r1-pd-1"], subject_digest="sha256:" + "0" * 64
    )
    (item,) = compute_review_debt(run, [stale])
    assert item.status == "open"
    assert item.cleared_by is None
    assert item.stale_attestation is True


def test_fresh_attestation_beats_a_stale_one_for_same_item():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    stale = _attestation(
        run,
        AttestationDecision.ACCEPT,
        ["r1-pd-1"],
        att_id="att-stale",
        subject_digest="sha256:" + "0" * 64,
    )
    fresh = _attestation(run, AttestationDecision.ACCEPT, ["r1-pd-1"], att_id="att-fresh")
    (item,) = compute_review_debt(run, [stale, fresh])
    assert item.status == "cleared"
    assert item.cleared_by == "att-fresh"
    assert item.stale_attestation is False


def test_subject_digest_is_versioned_and_content_bound():
    # Finding 4: the digest is over the immutable canonical subject, so adding a
    # defaulted field to AgentRun does NOT stale historical attestations, while
    # changing reviewed content does.
    from aro_schema import Coverage, ReviewerSeat, StepRecord

    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    d1 = run_subject_digest(run)
    assert d1.startswith("v1:sha256:")

    run.coverage = Coverage(captured=["x"])
    run.reviewer_seats = [ReviewerSeat(id="s", name="n", role="r", scope="sc")]
    assert run_subject_digest(run) == d1  # schema additions do not change the subject

    run.steps.append(StepRecord(index=0, name="t", input_digest="sha256:" + "a" * 64))
    assert run_subject_digest(run) != d1  # changing step content does


def test_blank_identity_or_scope_is_refused_by_the_schema():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    for bad in ({"attested_by": "  "}, {"declared_scope": ""}):
        with pytest.raises(ValueError):
            Attestation(
                id="a",
                run_id="r1",
                decision=AttestationDecision.ACCEPT,
                declared_scope=bad.get("declared_scope", "scope"),
                attested_by=bad.get("attested_by", "Hao"),
                subject_digest=run_subject_digest(run),
            )
