from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InterventionArm:
    pair_id: str
    arm_type: str
    source_task: str
    intervention_policy: str | None = None
    trigger_index: int | None = None
    trigger_mode: str = "static_prefix"
    declared_efe_mode: str = "disabled_default_sweagent"
    arm_id: str = ""
    source_family: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterventionArm":
        known = {
            "pair_id",
            "arm_type",
            "source_task",
            "source_task_id",
            "intervention_policy",
            "intervention_policy_id",
            "trigger_index",
            "injection_trigger_after_prefix_index",
            "trigger_mode",
            "declared_efe_mode",
            "arm_id",
            "source_family",
        }
        trigger = data.get("trigger_index", data.get("injection_trigger_after_prefix_index"))
        return cls(
            pair_id=str(data.get("pair_id") or ""),
            arm_type=str(data.get("arm_type") or ""),
            source_task=str(data.get("source_task") or data.get("source_task_id") or ""),
            intervention_policy=data.get("intervention_policy")
            or data.get("intervention_policy_id"),
            trigger_index=int(trigger) if trigger is not None else None,
            trigger_mode=str(data.get("trigger_mode") or "static_prefix"),
            declared_efe_mode=str(data.get("declared_efe_mode") or "disabled_default_sweagent"),
            arm_id=str(data.get("arm_id") or ""),
            source_family=str(data.get("source_family") or ""),
            metadata={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data.update(
            {
                "pair_id": self.pair_id,
                "arm_type": self.arm_type,
                "source_task": self.source_task,
                "intervention_policy": self.intervention_policy,
                "trigger_index": self.trigger_index,
                "trigger_mode": self.trigger_mode,
                "declared_efe_mode": self.declared_efe_mode,
                "arm_id": self.arm_id,
                "source_family": self.source_family,
            }
        )
        return data


@dataclass
class InterventionPair:
    pair_id: str
    control: InterventionArm
    intervention: InterventionArm
    source_family: str = ""

    @classmethod
    def from_arms(cls, arms: list[InterventionArm]) -> "InterventionPair":
        by_type = {arm.arm_type: arm for arm in arms}
        if "control" not in by_type or "intervention" not in by_type:
            raise ValueError("InterventionPair requires one control and one intervention arm")
        return cls(
            pair_id=by_type["control"].pair_id or by_type["intervention"].pair_id,
            control=by_type["control"],
            intervention=by_type["intervention"],
            source_family=by_type["control"].source_family or by_type["intervention"].source_family,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "source_family": self.source_family,
            "control": self.control.to_dict(),
            "intervention": self.intervention.to_dict(),
        }


@dataclass
class InterventionResult:
    pair_id: str
    control_resolved: bool | None = None
    intervention_resolved: bool | None = None
    trigger_hit: bool = False
    injection_count: int = 0
    attribution: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_delta(self) -> int | None:
        if self.control_resolved is None or self.intervention_resolved is None:
            return None
        return int(self.intervention_resolved) - int(self.control_resolved)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterventionResult":
        return cls(
            pair_id=str(data.get("pair_id") or ""),
            control_resolved=data.get("control_resolved"),
            intervention_resolved=data.get("intervention_resolved"),
            trigger_hit=bool(data.get("trigger_hit", False)),
            injection_count=int(data.get("injection_count", 0) or 0),
            attribution=str(data.get("attribution") or ""),
            metadata={
                key: value
                for key, value in data.items()
                if key
                not in {
                    "pair_id",
                    "control_resolved",
                    "intervention_resolved",
                    "trigger_hit",
                    "injection_count",
                    "attribution",
                }
            },
        )
