from __future__ import annotations

from datetime import datetime, timezone

from typing import Any

from wutai_clinic.schemas import ControlledScorecard, DualScorecard, NativeScorecard

from .grammar_gate import classify_route

UTC = timezone.utc  # py3.10 compat: datetime.UTC is 3.11+


def score_response(response: str, expected_route: str) -> NativeScorecard:
    route = classify_route(response)
    return NativeScorecard(
        native_text_route_count=int(expected_route == "text" and route == "text"),
        native_text_route_total=int(expected_route == "text"),
        native_tool_route_count=int(expected_route == "tool" and route == "tool"),
        native_tool_route_total=int(expected_route == "tool"),
    )


def score_controlled(
    response: str,
    controller_policy: dict[str, Any] | None = None,
    telemetry: dict[str, Any] | None = None,
) -> ControlledScorecard:
    policy = controller_policy or {}
    telem = telemetry or {}
    return ControlledScorecard(
        runtime_gate_passed=bool(policy.get("runtime_gate_passed", True)),
        telemetry_gate_passed=bool(telem.get("telemetry_gate_passed", True)),
        behavior_controller_passed=bool(policy.get("behavior_controller_passed", True)),
        route_consistency=int(telem.get("route_consistency", 1)),
        route_consistency_total=int(telem.get("route_consistency_total", 1)),
        secret_persistence=bool(telem.get("secret_persistence", False)),
        raw_payload_persistence=bool(telem.get("raw_payload_persistence", False)),
    )


def native_scorecard_from_phase3a_report(report: dict[str, Any]) -> NativeScorecard:
    summary = report.get("fresh_generation_summary") or {}
    repairs = report.get("repair_summary") or {}
    return NativeScorecard(
        semantic_fallback_count=int(summary.get("semantic_fallback_count", 0) or 0),
        tool_call_repair_count=int(repairs.get("tool_call_repair_count", 0) or 0),
        tool_name_repair_count=int(repairs.get("tool_name_repair_count", 0) or 0),
        native_text_route_count=int(summary.get("native_text_route_count", 0) or 0),
        native_text_route_total=int(summary.get("text_record_count", 0) or 0),
        native_tool_route_count=int(summary.get("native_tool_route_count", 0) or 0),
        native_tool_route_total=int(summary.get("tool_record_count", 0) or 0),
    )


def controlled_scorecard_from_phase3a_report(report: dict[str, Any]) -> ControlledScorecard:
    summary = report.get("fresh_generation_summary") or {}
    gates = report.get("gate_summary") or {}
    checks = (report.get("controlled_regression_gate") or {}).get("checks") or {}
    route_consistency = int(summary.get("native_text_route_count", 0) or 0) + int(
        summary.get("native_tool_route_count", 0) or 0
    )
    return ControlledScorecard(
        runtime_gate_passed=bool(
            gates.get("runtime_gate_passed", checks.get("runtime_gate_passed", False))
        ),
        telemetry_gate_passed=bool(
            gates.get("telemetry_monitor_passed", checks.get("telemetry_monitor_passed", False))
        ),
        behavior_controller_passed=bool(
            gates.get(
                "behavior_controller_gate_passed",
                checks.get("behavior_controller_gate_passed", False),
            )
        ),
        route_consistency=route_consistency,
        route_consistency_total=int(summary.get("record_count", 0) or 0),
        secret_persistence=not bool(checks.get("no_raw_or_gated_payload_stored", True)),
        raw_payload_persistence=not bool(checks.get("no_raw_or_gated_payload_stored", True)),
    )


def dual_scorecard_from_phase3a_report(report: dict[str, Any]) -> DualScorecard:
    inputs = report.get("inputs") or {}
    return DualScorecard(
        native=native_scorecard_from_phase3a_report(report),
        controlled=controlled_scorecard_from_phase3a_report(report),
        timestamp=str(report.get("generated_at") or ""),
        adapter_path=str(inputs.get("adapter_path") or ""),
        eval_suite=str(inputs.get("eval_suite") or inputs.get("rollout_report") or ""),
    )


def score_suite(responses: list[dict[str, Any]], eval_suite: list[dict[str, Any]]) -> DualScorecard:
    expected = {str(item.get("id")): str(item.get("expected_route", "text")) for item in eval_suite}
    native = NativeScorecard()
    for row in responses:
        probe_id = str(row.get("id"))
        scored = score_response(str(row.get("response", "")), expected.get(probe_id, "text"))
        native.native_text_route_count += scored.native_text_route_count
        native.native_text_route_total += scored.native_text_route_total
        native.native_tool_route_count += scored.native_tool_route_count
        native.native_tool_route_total += scored.native_tool_route_total
        native.semantic_fallback_count += int(row.get("semantic_fallback_used", False))
        action = str(row.get("tool_grammar_action") or row.get("grammar_action") or "")
        native.tool_call_repair_count += int(action == "tool_call_repair")
        native.tool_name_repair_count += int(action == "tool_name_repair")
    controlled = ControlledScorecard(
        runtime_gate_passed=True,
        telemetry_gate_passed=True,
        behavior_controller_passed=True,
        route_consistency=native.native_text_route_count + native.native_tool_route_count,
        route_consistency_total=len(responses),
    )
    return DualScorecard(
        native=native,
        controlled=controlled,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )
