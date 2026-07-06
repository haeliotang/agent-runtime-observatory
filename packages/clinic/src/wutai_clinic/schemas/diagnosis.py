from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurningPointCandidate:
    prefix_index: int
    state_class: Any = ""
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    prefix_sha256: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurningPointCandidate":
        return cls(
            prefix_index=int(data.get("prefix_index", 0) or 0),
            state_class=data.get("state_class", ""),
            feature_snapshot=dict(
                data.get("feature_snapshot")
                or data.get("prefix_only_context")
                or data.get("features")
                or {}
            ),
            confidence=float(data.get("confidence", data.get("diagnostic_score", 0.0)) or 0.0),
            reason_codes=[str(item) for item in data.get("reason_codes", [])],
            prefix_sha256=str(data.get("prefix_sha256") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefix_index": self.prefix_index,
            "state_class": self.state_class,
            "feature_snapshot": self.feature_snapshot,
            "confidence": self.confidence,
            "reason_codes": self.reason_codes,
            "prefix_sha256": self.prefix_sha256,
        }


@dataclass
class TrajectoryDiagnosis:
    instance_id: str
    candidates: list[TurningPointCandidate]
    turn_count: int = 0
    outcome_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryDiagnosis":
        candidate_rows = data.get("candidates")
        if candidate_rows is None:
            candidate_rows = data.get("top_transition_candidates") or []
        outcome_context = data.get("outcome_context_for_audit_only") or {}
        outcome_label = data.get("outcome_label") or outcome_context.get("outcome_class")
        known = {
            "instance_id",
            "trajectory_id",
            "source_task_id",
            "candidates",
            "top_transition_candidates",
            "turn_count",
            "outcome_label",
            "outcome_context_for_audit_only",
        }
        return cls(
            instance_id=str(
                data.get("instance_id")
                or data.get("trajectory_id")
                or data.get("source_task_id")
                or ""
            ),
            candidates=[TurningPointCandidate.from_dict(item) for item in candidate_rows],
            turn_count=int(data.get("turn_count", 0) or 0),
            outcome_label=str(outcome_label) if outcome_label is not None else None,
            metadata={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data.update(
            {
                "instance_id": self.instance_id,
                "candidates": [candidate.to_dict() for candidate in self.candidates],
                "turn_count": self.turn_count,
                "outcome_label": self.outcome_label,
            }
        )
        return data
