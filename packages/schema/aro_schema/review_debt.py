"""Review-debt derivation: join a run's needs_review decisions against its
attestations.

Semantics (documented in docs/object-model.md):
- a debt item exists for every ``needs_review`` PolicyDecision;
- it is cleared iff an ``accept`` or ``amend`` attestation names its decision
  id in ``clears_decisions`` **and** that attestation's ``subject_digest`` still
  matches the current run's *canonical subject* (see below) — clearing is bound
  to the reviewed content, so replacing the run with different content reopens
  the debt (the item goes back to open, flagged ``stale_attestation``);
- a ``reject`` attestation clears nothing — the seat stays visibly empty;
- an empty ``clears_decisions`` is a run-level endorsement and clears nothing.

The subject digest is over a **versioned canonical subject** built from an
explicit per-version allowlist — not the raw ``model_dump_json()`` (which is
schema-fragile) and not a minimal core (which under-binds). The allowlist names
exactly the fields that define *what a human reviewed*, so changing any of them
reopens the debt, while volatile serialization (timestamps, computed verdict,
coverage) is excluded so it does not spuriously stale.

Version policy (see docs/object-model.md):
- new attestations are written with ``SUBJECT_SCHEMA_VERSION`` (currently 2);
- ``v2`` binds run identity, reviewer seats, per-step digests/error, and the
  *full* policy decisions (id, policy_id, rule_id, decision, reason);
- only versions in ``_CLEARING_VERSIONS`` still grant clearing power. **``v1``
  is revoked**: it under-bound (it did not cover reviewer seats, decision ids,
  policy_id, or reason), so a ``v1`` attestation never clears — it must be
  re-attested under ``v2``. Adding a future ``v3`` is likewise an explicit
  decision about whether ``v2`` keeps its clearing power.
"""

from __future__ import annotations

import re
from typing import Any

from aro_schema.digests import digest_obj
from aro_schema.models import (
    AgentRun,
    Attestation,
    AttestationDecision,
    Decision,
    ReviewDebtItem,
)

SUBJECT_SCHEMA_VERSION = 2
# Versions that still grant clearing power. v1 is revoked (it under-bound the
# reviewed record); a v1 attestation is treated as stale and must be re-attested.
_CLEARING_VERSIONS = frozenset({2})
_VERSION_RE = re.compile(r"^v(\d+):")


def _canonical_subject(run: AgentRun, version: int) -> dict[str, Any]:
    if version == 2:
        return {
            "v": 2,
            "run_id": run.id,
            "task_id": run.task_id,
            "agent": run.agent,
            "model": run.model,
            "reviewer_seats": [[s.id, s.name, s.role, s.scope] for s in run.reviewer_seats],
            "steps": [[s.index, s.input_digest, s.output_digest, s.error] for s in run.steps],
            "policy_decisions": [
                [d.id, d.step_index, d.policy_id, d.rule_id, d.decision.value, d.reason]
                for d in run.policy_decisions
            ],
        }
    if version == 1:
        # Retained for historical re-derivation only; v1 no longer clears.
        return {
            "v": 1,
            "run_id": run.id,
            "steps": [[s.index, s.input_digest, s.output_digest, s.error] for s in run.steps],
            "policy_decisions": [
                [d.step_index, d.rule_id, d.decision.value] for d in run.policy_decisions
            ],
        }
    raise ValueError(f"unknown subject schema version: {version}")


def run_subject_digest(run: AgentRun, version: int = SUBJECT_SCHEMA_VERSION) -> str:
    """The versioned canonical digest an attestation must match to clear debt.

    Canonical across the codebase: the API stamps a new attestation with this,
    and ``subject_matches`` re-derives it (for the attestation's own version) to
    detect content drift. Both sides call this one function."""
    return f"v{version}:{digest_obj(_canonical_subject(run, version))}"


def subject_matches(run: AgentRun, subject_digest: str) -> bool:
    """True iff ``subject_digest`` matches ``run`` under a version that still
    grants clearing power. An unversioned, unknown, or revoked version (e.g. v1)
    never matches — it is treated as stale."""
    match = _VERSION_RE.match(subject_digest or "")
    if not match:
        return False
    version = int(match.group(1))
    if version not in _CLEARING_VERSIONS:
        return False
    try:
        return run_subject_digest(run, version) == subject_digest
    except ValueError:
        return False


def compute_review_debt(run: AgentRun, attestations: list[Attestation]) -> list[ReviewDebtItem]:
    """Return one ReviewDebtItem per needs_review decision, joined to whichever
    accept/amend attestation (if any) cleared it under the current subject.
    First valid clearing attestation wins."""
    cleared_by: dict[str, Attestation] = {}
    stale_named: set[str] = set()
    for attestation in attestations:
        if attestation.decision == AttestationDecision.REJECT:
            continue
        fresh = subject_matches(run, attestation.subject_digest)
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
