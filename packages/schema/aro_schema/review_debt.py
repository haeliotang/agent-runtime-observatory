"""Review-debt derivation: join a run's needs_review decisions against its
attestations.

Semantics (documented in docs/object-model.md):
- a debt item exists for every ``needs_review`` PolicyDecision;
- it is cleared iff an ``accept`` or ``amend`` attestation names its decision
  id in ``clears_decisions`` **and** that attestation's ``subject_digest``
  still matches the current stored run — clearing is bound to the exact record
  reviewed, so overwriting the run after the fact does not silently keep the
  debt cleared (the item goes back to open, flagged ``stale_attestation``);
- a ``reject`` attestation clears nothing — the seat stays visibly empty;
- an attestation with an empty ``clears_decisions`` is a run-level endorsement
  and clears nothing (honesty: standing behind the run as a whole is not the
  same act as reviewing a specific flagged step).
"""

from __future__ import annotations

from aro_schema.digests import digest_text
from aro_schema.models import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Decision,
    ReviewDebtItem,
)


def run_subject_digest(run: AgentRun) -> str:
    """The digest an attestation must match to clear this run's debt.

    Canonical across the codebase: the API stamps a new attestation with this,
    and ``compute_review_debt`` re-derives it to detect drift. Both sides call
    this one function so the comparison can never disagree by construction.
    """
    return digest_text(run.model_dump_json())


def compute_review_debt(run: AgentRun, attestations: list[Attestation]) -> list[ReviewDebtItem]:
    """Return one ReviewDebtItem per needs_review decision, joined to whichever
    accept/amend attestation (if any) cleared it under the current run digest.
    First valid clearing attestation wins."""
    run_digest = run_subject_digest(run)
    cleared_by: dict[str, Attestation] = {}
    stale_named: set[str] = set()
    for attestation in attestations:
        if attestation.decision == AttestationDecision.REJECT:
            continue
        fresh = attestation.subject_digest == run_digest
        for decision_id in attestation.clears_decisions:
            if fresh:
                cleared_by.setdefault(decision_id, attestation)
            else:
                stale_named.add(decision_id)

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
                stale_attestation=clearing is None and decision.id in stale_named,
            )
        )
    return items
