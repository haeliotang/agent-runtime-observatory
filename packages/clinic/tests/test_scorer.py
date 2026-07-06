from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.engine.scorer import (
    dual_scorecard_from_phase3a_report,
    native_scorecard_from_phase3a_report,
    score_suite,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def _load_report(name: str) -> dict:
    return json.loads((MODELS / name).read_text())


def test_phase3a39_passed_report_maps_to_passing_dual_scorecard() -> None:
    report = _load_report("phase3a_controlled_regression_gate_report.json")
    scorecard = dual_scorecard_from_phase3a_report(report)
    assert scorecard.native.passed
    assert scorecard.controlled.passed
    assert scorecard.passed
    assert scorecard.native.native_text_route_count == 16
    assert scorecard.native.native_text_route_total == 16
    assert scorecard.native.native_tool_route_count == 16
    assert scorecard.native.native_tool_route_total == 16
    assert scorecard.native.semantic_fallback_count == 0


def test_phase3a39_blocked_report_preserves_native_failure_reasons() -> None:
    report = _load_report("phase3b_full_checkpoint50_controlled_regression_report.json")
    scorecard = dual_scorecard_from_phase3a_report(report)
    assert not scorecard.native.passed
    assert not scorecard.controlled.passed
    assert not scorecard.passed
    assert scorecard.native.semantic_fallback_count == 7
    assert scorecard.native.native_text_route_count == 2
    assert scorecard.native.native_text_route_total == 16
    assert scorecard.native.native_tool_route_count == 16
    assert scorecard.native.native_tool_route_total == 16
    assert "semantic fallbacks: 7" in scorecard.native.to_table()


def test_native_scorecard_from_report_matches_fresh_generation_summary() -> None:
    report = _load_report("phase3b_full_checkpoint50_controlled_regression_report.json")
    summary = report["fresh_generation_summary"]
    native = native_scorecard_from_phase3a_report(report)
    assert native.semantic_fallback_count == summary["semantic_fallback_count"]
    assert native.native_text_route_count == summary["native_text_route_count"]
    assert native.native_text_route_total == summary["text_record_count"]
    assert native.native_tool_route_count == summary["native_tool_route_count"]
    assert native.native_tool_route_total == summary["tool_record_count"]


def test_score_suite_counts_repairs_and_semantic_fallbacks() -> None:
    responses = [
        {"id": "text_ok", "response": "plain answer"},
        {
            "id": "tool_ok",
            "response": '{"type":"tool_call","name":"run_command","arguments":{"command":"pwd"}}',
        },
        {
            "id": "text_fallback",
            "response": "plain fallback",
            "semantic_fallback_used": True,
        },
        {
            "id": "tool_repair",
            "response": '{"type":"tool_call","name":"run_command","arguments":{"command":"pwd"}}',
            "tool_grammar_action": "tool_call_repair",
        },
    ]
    suite = [
        {"id": "text_ok", "expected_route": "text"},
        {"id": "tool_ok", "expected_route": "tool"},
        {"id": "text_fallback", "expected_route": "text"},
        {"id": "tool_repair", "expected_route": "tool"},
    ]
    scorecard = score_suite(responses, suite)
    assert scorecard.native.semantic_fallback_count == 1
    assert scorecard.native.tool_call_repair_count == 1
    assert scorecard.native.native_text_route_count == 2
    assert scorecard.native.native_tool_route_count == 2
    assert not scorecard.native.passed
