from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceEntry:
    path: str
    sha256: str = ""
    rows: int | None = None
    role: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceEntry":
        return cls(
            path=str(data.get("path") or data.get("artifact") or ""),
            sha256=str(data.get("sha256") or ""),
            rows=data.get("rows") or data.get("line_count"),
            role=str(data.get("role") or ""),
            metadata={
                key: value
                for key, value in data.items()
                if key not in {"path", "artifact", "sha256", "rows", "line_count", "role"}
            },
        )


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(cls, name: str, value: Any) -> "GateResult":
        if isinstance(value, dict):
            passed = bool(value.get("passed", value.get("ok", False)))
            detail = str(value.get("detail") or value.get("reason") or "")
            metadata = {
                key: item
                for key, item in value.items()
                if key not in {"passed", "ok", "detail", "reason"}
            }
            return cls(name=name, passed=passed, detail=detail, metadata=metadata)
        return cls(name=name, passed=bool(value))


@dataclass
class Report:
    phase: str = ""
    decision: str = ""
    gates: list[GateResult] = field(default_factory=list)
    claim_boundary: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, data: dict[str, Any]) -> "Report":
        gate_rows: list[GateResult] = []
        for key in ("gates", "evidence_gate", "gate_results"):
            value = data.get(key)
            if isinstance(value, dict):
                gate_rows.extend(GateResult.from_item(name, item) for name, item in value.items())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        gate_rows.append(
                            GateResult.from_item(str(item.get("name") or "gate"), item)
                        )
        known = {"phase", "decision", "gates", "evidence_gate", "gate_results", "claim_boundary"}
        return cls(
            phase=str(data.get("phase") or ""),
            decision=str(data.get("decision") or ""),
            gates=gate_rows,
            claim_boundary=data.get("claim_boundary"),
            metadata={key: value for key, value in data.items() if key not in known},
        )

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)


@dataclass
class Manifest:
    phase: str = ""
    artifacts: list[EvidenceEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        raw_artifacts = data.get("artifacts") or data.get("outputs") or []
        artifacts = [
            EvidenceEntry.from_dict(item if isinstance(item, dict) else {"path": str(item)})
            for item in raw_artifacts
        ]
        return cls(
            phase=str(data.get("phase") or ""),
            artifacts=artifacts,
            metadata={
                key: value
                for key, value in data.items()
                if key not in {"phase", "artifacts", "outputs"}
            },
        )
