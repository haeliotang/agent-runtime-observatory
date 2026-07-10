"""Review-debt derivation: join a run's needs_review decisions against its
attestations.

Semantics (documented in docs/object-model.md):
- a debt item exists for every ``needs_review`` PolicyDecision;
- it is cleared iff an ``accept`` or ``amend`` attestation names its decision
  id in ``clears_decisions``;
- a ``reject`` attestation clears nothing — the seat stays visibly empty;
- an attestation with an empty ``clears_decisions`` is a run-level endorsement
  and clears nothing (honesty: standing behind the run as a whole is not the
  same act as reviewing a specific flagged step).
"""

from __future__ import annotations

from aro_schema.models import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Decision,
    ReviewDebtItem,
)


def compute_review_debt(run: AgentRun, attestations: list[Attestation]) -> list[ReviewDebtItem]:
    """Return one ReviewDebtItem per needs_review decision, joined to whichever
    accept/amend attestation (if any) cleared it. First clearing attestation
    wins; later ones are recorded facts but do not re-clear."""
    cleared_by: dict[str, Attestation] = {}
    for attestation in attestations:
        if attestation.decision == AttestationDecision.REJECT:
            continue
        for decision_id in attestation.clears_decisions:
            cleared_by.setdefault(decision_id, attestation)

    items: list[ReviewDebtItem] = []
    for decision in run.policy_decisions:
        if decision.decision != Decision.NEEDS_REVIEW:
            continue
        clearing = cleared_by.get(decision.id)
        items.append(
            ReviewDebtItem(
                decision_id=decision.id,
                run_id=run.id,
                step_index=decision.step_index,
                rule_id=decision.rule_id,
                reason=decision.reason,
                status="cleared" if clearing else "open",
                cleared_by=clearing.id if clearing else None,
                attested_by=clearing.attested_by if clearing else None,
            )
        )
    return items
