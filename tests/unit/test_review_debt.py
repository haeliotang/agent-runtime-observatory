"""Semantics of consumable review debt (see docs/object-model.md):
debt derives from needs_review decisions; accept/amend clears named items;
reject clears nothing; run-level attestations clear nothing."""

from aro_schema import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Decision,
    PolicyDecision,
    compute_review_debt,
    digest_text,
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


def _attestation(decision: AttestationDecision, clears: list[str], att_id: str = "att-1"):
    return Attestation(
        id=att_id,
        run_id="r1",
        decision=decision,
        declared_scope="scope",
        attested_by="Hao",
        subject_digest=digest_text("x"),
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
    items = compute_review_debt(run, [_attestation(AttestationDecision.ACCEPT, ["r1-pd-1"])])
    by_id = {item.decision_id: item for item in items}
    assert by_id["r1-pd-1"].status == "cleared"
    assert by_id["r1-pd-1"].cleared_by == "att-1"
    assert by_id["r1-pd-1"].attested_by == "Hao"
    assert by_id["r1-pd-3"].status == "open"  # not named -> still open


def test_reject_never_clears():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    items = compute_review_debt(run, [_attestation(AttestationDecision.REJECT, ["r1-pd-1"])])
    assert items[0].status == "open"  # the seat stays visibly empty


def test_run_level_attestation_clears_nothing():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    items = compute_review_debt(run, [_attestation(AttestationDecision.ACCEPT, [])])
    assert items[0].status == "open"


def test_first_clearing_attestation_wins():
    run = _run(_decision(1, Decision.NEEDS_REVIEW))
    first = _attestation(AttestationDecision.ACCEPT, ["r1-pd-1"], att_id="att-first")
    second = _attestation(AttestationDecision.AMEND, ["r1-pd-1"], att_id="att-second")
    items = compute_review_debt(run, [first, second])
    assert items[0].cleared_by == "att-first"
