from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _public_dict(value: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in known}


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ToolCall | None":
        if not data:
            return None
        known = {"name", "arguments"}
        return cls(
            name=str(data.get("name") or ""),
            arguments=dict(data.get("arguments") or {}),
            metadata=_public_dict(data, known),
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data["name"] = self.name
        data["arguments"] = self.arguments
        return data


@dataclass
class Turn:
    role: str
    content: Any = ""
    tool_call: ToolCall | None = None
    reasoning: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn":
        known = {"role", "content", "tool_call", "reasoning"}
        return cls(
            role=str(data.get("role") or ""),
            content=data.get("content", ""),
            tool_call=ToolCall.from_dict(data.get("tool_call")),
            reasoning=data.get("reasoning"),
            metadata=_public_dict(data, known),
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data["role"] = self.role
        data["content"] = self.content
        if self.reasoning is not None:
            data["reasoning"] = self.reasoning
        if self.tool_call is not None:
            data["tool_call"] = self.tool_call.to_dict()
        return data


@dataclass
class STRHealth:
    online_str_v1: float = 0.0
    recurrence_slope: float = 0.0
    recurrence_peak: float = 0.0
    recurrence_persistence: float = 0.0
    duplicate_ratio: float = 0.0
    action_entropy: float = 0.0
    error_streak: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "STRHealth | None":
        if not data:
            return None
        known = {
            "online_str_v1",
            "recurrence_slope",
            "recurrence_peak",
            "recurrence_persistence",
            "duplicate_ratio",
            "action_entropy",
            "error_streak",
        }
        return cls(
            online_str_v1=float(data.get("online_str_v1", 0.0) or 0.0),
            recurrence_slope=float(data.get("recurrence_slope", 0.0) or 0.0),
            recurrence_peak=float(data.get("recurrence_peak", 0.0) or 0.0),
            recurrence_persistence=float(data.get("recurrence_persistence", 0.0) or 0.0),
            duplicate_ratio=float(data.get("duplicate_ratio", 0.0) or 0.0),
            action_entropy=float(data.get("action_entropy", 0.0) or 0.0),
            error_streak=int(data.get("error_streak", 0) or 0),
            metadata=_public_dict(data, known),
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data.update(
            {
                "online_str_v1": self.online_str_v1,
                "recurrence_slope": self.recurrence_slope,
                "recurrence_peak": self.recurrence_peak,
                "recurrence_persistence": self.recurrence_persistence,
                "duplicate_ratio": self.duplicate_ratio,
                "action_entropy": self.action_entropy,
                "error_streak": self.error_streak,
            }
        )
        return data


@dataclass
class Trajectory:
    instance_id: str
    sft_turns: list[Turn]
    environment: str = "unknown"
    source: str = "unknown"
    task: str = ""
    str_health_v1: STRHealth | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id_field: str | None = None
    turns_field: str = "sft_turns"
    has_source_field: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trajectory":
        turns = data.get("sft_turns")
        turns_field = "sft_turns"
        if turns is None:
            turns = data.get("trajectory") or []
            turns_field = "trajectory"
        known = {
            "instance_id",
            "trajectory_id",
            "environment",
            "source",
            "task",
            "sft_turns",
            "trajectory",
            "str_health_v1",
        }
        instance_id = (
            data.get("instance_id")
            or data.get("trajectory_id")
            or data.get("environment")
            or data.get("_wutai_purified_index")
            or ""
        )
        id_field = None
        if "instance_id" in data:
            id_field = "instance_id"
        elif "trajectory_id" in data:
            id_field = "trajectory_id"
        return cls(
            instance_id=str(instance_id),
            sft_turns=[Turn.from_dict(item) for item in turns],
            environment=str(data.get("environment") or "unknown"),
            source=str(data.get("source") or data.get("_wutai_source_file") or "unknown"),
            task=str(data.get("task") or ""),
            str_health_v1=STRHealth.from_dict(data.get("str_health_v1")),
            metadata=_public_dict(data, known),
            id_field=id_field,
            turns_field=turns_field,
            has_source_field="source" in data,
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        if self.environment != "unknown":
            data["environment"] = self.environment
        if self.task:
            data["task"] = self.task
        if self.has_source_field:
            data["source"] = self.source
        if self.id_field and self.instance_id:
            data[self.id_field] = self.instance_id
        data[self.turns_field] = [turn.to_dict() for turn in self.sft_turns]
        if self.str_health_v1 is not None:
            data["str_health_v1"] = self.str_health_v1.to_dict()
        return data
