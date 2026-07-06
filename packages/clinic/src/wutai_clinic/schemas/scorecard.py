from __future__ import annotations

from dataclasses import dataclass


def _ratio_text(count: int, total: int) -> str:
    return f"{count}/{total}" if total else "0/0"


@dataclass
class NativeScorecard:
    semantic_fallback_count: int = 0
    tool_call_repair_count: int = 0
    tool_name_repair_count: int = 0
    native_text_route_count: int = 0
    native_text_route_total: int = 0
    native_tool_route_count: int = 0
    native_tool_route_total: int = 0

    @property
    def passed(self) -> bool:
        return (
            self.semantic_fallback_count == 0
            and self.tool_call_repair_count == 0
            and self.tool_name_repair_count == 0
            and self.native_text_route_count == self.native_text_route_total
            and self.native_tool_route_count == self.native_tool_route_total
        )

    def to_table(self) -> str:
        return "\n".join(
            [
                "Native scorecard",
                f"passed: {self.passed}",
                f"text route: {_ratio_text(self.native_text_route_count, self.native_text_route_total)}",
                f"tool route: {_ratio_text(self.native_tool_route_count, self.native_tool_route_total)}",
                f"semantic fallbacks: {self.semantic_fallback_count}",
                f"tool-call repairs: {self.tool_call_repair_count}",
                f"tool-name repairs: {self.tool_name_repair_count}",
            ]
        )


@dataclass
class ControlledScorecard:
    runtime_gate_passed: bool = False
    telemetry_gate_passed: bool = False
    behavior_controller_passed: bool = False
    route_consistency: int = 0
    route_consistency_total: int = 0
    secret_persistence: bool = False
    raw_payload_persistence: bool = False

    @property
    def passed(self) -> bool:
        return (
            self.runtime_gate_passed
            and self.telemetry_gate_passed
            and self.behavior_controller_passed
            and self.route_consistency == self.route_consistency_total
            and not self.secret_persistence
            and not self.raw_payload_persistence
        )

    def to_table(self) -> str:
        return "\n".join(
            [
                "Controlled scorecard",
                f"passed: {self.passed}",
                f"runtime gate: {self.runtime_gate_passed}",
                f"telemetry gate: {self.telemetry_gate_passed}",
                f"behavior controller: {self.behavior_controller_passed}",
                f"route consistency: {_ratio_text(self.route_consistency, self.route_consistency_total)}",
                f"secret persistence: {self.secret_persistence}",
                f"raw payload persistence: {self.raw_payload_persistence}",
            ]
        )


@dataclass
class DualScorecard:
    native: NativeScorecard
    controlled: ControlledScorecard
    timestamp: str = ""
    adapter_path: str = ""
    eval_suite: str = ""

    @property
    def passed(self) -> bool:
        return self.native.passed and self.controlled.passed

    def to_table(self) -> str:
        header = [
            "Dual scorecard",
            f"passed: {self.passed}",
            f"timestamp: {self.timestamp}",
            f"adapter: {self.adapter_path}",
            f"eval suite: {self.eval_suite}",
        ]
        return "\n".join(header + ["", self.native.to_table(), "", self.controlled.to_table()])
