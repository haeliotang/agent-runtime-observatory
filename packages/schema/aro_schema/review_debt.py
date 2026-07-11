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

The subject digest is over a **versioned canonical subject** — only the
immutable core (run id, per-step input/output digests and error, policy
decisions) — not the raw ``model_dump_json()``. This makes it stable across
schema evolution (adding a defaulted field to AgentRun does not stale historical
attestations) while still changing when the reviewed *content* changes. The
version is carried in the digest (``v1:sha256:...``); an attestation is compared
under the version it was written with, so a future ``v2`` never auto-stales
``v1`` attestations.
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

SUBJECT_SCHEMA_VERSION = 1
_VERSION_RE = re.compile(r"^v(\d+):")


def _canonical_subject(run: AgentRun, version: int) -> dict[str, Any]:
    if version == 1:
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
    """True iff ``subject_digest`` matches ``run`` under the version it encodes.

    An unversioned or unknown-version digest never matches (treated as stale)."""
    match = _VERSION_RE.match(subject_digest or "")
    if not match:
        return False
    version = int(match.group(1))
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
