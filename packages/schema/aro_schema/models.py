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

from pydantic import BaseModel, Field


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


class ReviewerSeat(BaseModel):
    """A named human seat that carries accountability for a scope of agent work."""

    id: str
    name: str
    role: str
    scope: str


class Goal(BaseModel):
    """What the human actually asked for, with constraints, owned by a seat."""

    id: str
    statement: str
    constraints: list[str] = Field(default_factory=list)
    owner_seat_id: str


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
    input_digest: str
    output_digest: str | None = None
    output_preview: str | None = None
    decision_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    duration_ms: float = 0.0
    error: str | None = None


class AgentRun(BaseModel):
    id: str
    task_id: str
    agent: str
    model: str | None = None
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    risk_signals: list[RiskSignal] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


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
