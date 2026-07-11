"""Core object model for the agent runtime observatory.

Design rules:
- every object is serializable and diffable;
- content is addressed by sha256 digest, not by timestamp;
- accountability is explicit: every Goal has an owning ReviewerSeat, every
  gated step points at the PolicyDecision that gated it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepKind(StrEnum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    POLICY_CHECK = "policy_check"
    ARTIFACT_WRITE = "artifact_write"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_REVIEW = "needs_review"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RunVerdict(StrEnum):
    """Run-level trust roll-up, vocabulary aligned with wutai's trust verdict."""

    TRUSTED = "trusted"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class AttestationDecision(StrEnum):
    """The human decision vocabulary, aligned with stillmirror-review's
    ratify flow (accept / amend / reject) — deliberately distinct from the
    machine-gate vocabulary (allow / deny / needs_review)."""

    ACCEPT = "accept"
    AMEND = "amend"
    REJECT = "reject"


class GoalEventKind(StrEnum):
    """Goal lifecycle vocabulary, aligned with stillmirror-review's
    append-only goal-events log."""

    INTRODUCED = "introduced"
    REINFORCED = "reinforced"
    REPLACED = "replaced"
    RETIRED = "retired"


# Controlled vocabulary for EvidenceItem.kind / artifact roles, adopted from
# wutai's artifactRole taxonomy (see docs/object-model-alignment.md).
EVIDENCE_ROLES = (
    "tool_output",
    "primary_artifact",
    "source_ledger",
    "claim_ledger",
    "evidence_verification",
    "policy_preflight",
    "policy_override_review",
    "trust_verdict",
    "runtime_trace",
    "file_inventory",
    "file_hash_check",
    "session_ledger",
    "audit_trail",
    "supporting_artifact",
)


class ReviewerSeat(BaseModel):
    """A named human seat that carries accountability for a scope of agent work.

    Every field is non-blank: a seat with an empty id/name/role/scope is a
    vacuous seat, and accountability that points at nothing is not
    accountability. Uniqueness of ids within a run is enforced on the Script.
    """

    id: str
    name: str
    role: str
    scope: str

    @field_validator("id", "name", "role", "scope")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("reviewer seat fields (id, name, role, scope) must not be blank")
        return value


class Goal(BaseModel):
    """What the human actually asked for, with constraints, owned by a seat."""

    id: str
    statement: str
    constraints: list[str] = Field(default_factory=list)
    owner_seat_id: str


class GoalEvent(BaseModel):
    """One entry in a goal's append-only lifecycle log.

    Ported from stillmirror-review's goal-events.jsonl: a goal is not a static
    string but something that gets introduced, reinforced by work, replaced,
    or retired — and provenance means recording *when* each happened.
    """

    id: str
    goal_id: str
    kind: GoalEventKind
    at: datetime = Field(default_factory=utcnow)
    note: str = ""
    replaced_by_goal_id: str | None = None


class Task(BaseModel):
    id: str
    title: str
    goal_id: str
    created_at: datetime = Field(default_factory=utcnow)


class PolicyDecision(BaseModel):
    """The outcome of evaluating one step against the active policy bundle."""

    id: str
    run_id: str
    step_index: int
    policy_id: str
    rule_id: str
    decision: Decision
    reason: str


class RiskSignal(BaseModel):
    id: str
    run_id: str
    step_index: int
    severity: Severity
    category: str
    message: str


class EvidenceItem(BaseModel):
    """A content-addressed pointer to something a claim about the run rests on."""

    id: str
    run_id: str
    step_index: int
    kind: str
    digest: str
    uri: str | None = None
    description: str = ""


class Artifact(BaseModel):
    id: str
    run_id: str
    path: str
    digest: str
    media_type: str = "text/plain"
    size_bytes: int


class StepRecord(BaseModel):
    """One executed (or blocked) step of an agent run.

    input_digest covers (tool name, resolved args); output_digest covers the
    tool output text. A blocked step has no output_digest and carries the
    blocking error instead.
    """

    index: int
    kind: StepKind = StepKind.TOOL_CALL
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    allocated_to: list[str] = Field(default_factory=list)
    supports_goal: str = "unknown"  # "yes" | "no" | "unknown", per stillmirror's ledger
    input_digest: str
    output_digest: str | None = None
    output_preview: str | None = None
    decision_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    duration_ms: float = 0.0
    error: str | None = None


class Coverage(BaseModel):
    """A run's own declaration of its observability limits.

    Ported from wutai's WorkPacketCoverage: an honest record states not just
    what it captured but what it structurally could not see and what it did
    not enforce.
    """

    captured: list[str] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    enforcement: list[str] = Field(default_factory=list)


class AgentRun(BaseModel):
    id: str
    task_id: str
    agent: str
    model: str | None = None
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    coverage: Coverage | None = None
    reviewer_seats: list[ReviewerSeat] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    risk_signals: list[RiskSignal] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(self) -> RunVerdict:
        """Trust roll-up over policy decisions, matching wutai's aggregation:
        any deny -> blocked, else any needs_review -> review_required,
        else trusted."""
        decisions = {d.decision for d in self.policy_decisions}
        if Decision.DENY in decisions:
            return RunVerdict.BLOCKED
        if Decision.NEEDS_REVIEW in decisions:
            return RunVerdict.REVIEW_REQUIRED
        return RunVerdict.TRUSTED


class Attestation(BaseModel):
    """A named human standing behind a scope of a run — the act of filling a
    ReviewerSeat.

    Field union of wutai's ConsumerAttestation and stillmirror-review's
    ratify flow. Two invariants ported verbatim from the siblings:
    - scoped ratification (wutai): `declared_scope` says what IS ratified and
      `excluded_scope` says what is explicitly NOT — approval is never total;
    - draft is not attestation (stillmirror): an assistant may fill
      `proposed_by`, but only the named human in `attested_by` makes this an
      attestation, and a `reject` decision is recorded rather than retried.
    """

    id: str
    run_id: str
    seat_id: str | None = None
    decision: AttestationDecision
    declared_scope: str
    excluded_scope: str = ""
    labels: list[str] = Field(default_factory=list)
    proposed_by: str | None = None
    attested_by: str
    note: str = ""
    subject_digest: str
    attested_at: datetime = Field(default_factory=utcnow)
    # The specific needs_review PolicyDecision ids this attestation clears.
    # Debt consumption is per-item, not per-run: an empty list means the human
    # stood behind the run as a whole without clearing any specific debt item.
    # A `reject` attestation never clears debt (the seat stays visibly empty).
    clears_decisions: list[str] = Field(default_factory=list)

    @field_validator("attested_by", "declared_scope")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # A blank name or scope is not an attestation — it is an empty seat
        # wearing a name tag. The schema refuses to record it as either.
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value


class ReviewDebtItem(BaseModel):
    """One unit of review debt, with its consumable status.

    Derived, never stored: a debt item exists for every needs_review
    PolicyDecision on a run, and is `cleared` iff an accept/amend Attestation
    names its decision id in `clears_decisions`. The recorded run stays
    immutable; clearing debt is a new fact (the attestation), not an edit.
    """

    decision_id: str
    run_id: str
    step_index: int
    rule_id: str
    reason: str
    status: str = "open"  # "open" | "cleared"
    cleared_by: str | None = None  # attestation id
    attested_by: str | None = None  # the named human on that attestation
    # True when an attestation names this item but its subject_digest no longer
    # matches the stored run (the run was overwritten after it was attested).
    # The item stays open — drift is surfaced, not silently honored.
    stale_attestation: bool = False


class StepDivergence(BaseModel):
    """One field where a replayed run disagrees with the recorded trace."""

    step_index: int
    field: str
    recorded: str | None = None
    replayed: str | None = None


class ReplayReport(BaseModel):
    run_id: str
    replayed_at: datetime = Field(default_factory=utcnow)
    steps_compared: int = 0
    divergences: list[StepDivergence] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.divergences
