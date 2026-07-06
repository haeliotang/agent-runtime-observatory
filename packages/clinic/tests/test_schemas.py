from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.schemas import (
    ControlledScorecard,
    DualScorecard,
    InterventionArm,
    InterventionPair,
    NativeScorecard,
    Report,
    Trajectory,
    TrajectoryDiagnosis,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_trajectory_round_trip_first_100_has_no_data_loss() -> None:
    path = MODELS / "trajectories_purified.jsonl"
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
            if len(rows) == 100:
                break
    for row in rows:
        parsed = Trajectory.from_dict(row)
        serialized = parsed.to_dict()
        assert serialized == row


def test_phase3a39_scorecard_known_values() -> None:
    blocked_native = NativeScorecard(
        semantic_fallback_count=7,
        tool_call_repair_count=0,
        tool_name_repair_count=0,
        native_text_route_count=2,
        native_text_route_total=16,
        native_tool_route_count=16,
        native_tool_route_total=16,
    )
    assert not blocked_native.passed
    assert "semantic fallbacks: 7" in blocked_native.to_table()

    passing_native = NativeScorecard(
        native_text_route_count=16,
        native_text_route_total=16,
        native_tool_route_count=16,
        native_tool_route_total=16,
    )
    assert passing_native.passed

    blocked_controlled = ControlledScorecard(
        runtime_gate_passed=False,
        telemetry_gate_passed=True,
        behavior_controller_passed=True,
        route_consistency=32,
        route_consistency_total=32,
        secret_persistence=False,
        raw_payload_persistence=False,
    )
    assert not blocked_controlled.passed

    passing_controlled = ControlledScorecard(
        runtime_gate_passed=True,
        telemetry_gate_passed=True,
        behavior_controller_passed=True,
        route_consistency=32,
        route_consistency_total=32,
    )
    assert DualScorecard(passing_native, passing_controlled).passed


def test_legacy_diagnosis_candidates_parse() -> None:
    path = MODELS / "phase311_trajectory_diagnosis_candidates.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    diagnosis = TrajectoryDiagnosis.from_dict(row)
    assert diagnosis.instance_id
    assert diagnosis.candidates
    assert diagnosis.candidates[0].prefix_index > 0


def test_intervention_schedule_pair_parse() -> None:
    path = MODELS / "phase316_paired_uplift_execution_schedule.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()[:2]]
    arms = [InterventionArm.from_dict(row) for row in rows]
    pair = InterventionPair.from_arms(arms)
    assert pair.control.arm_type == "control"
    assert pair.intervention.arm_type == "intervention"


def test_report_from_legacy() -> None:
    paths = [
        MODELS / "phase310_str_early_warning_pilot_report.json",
        MODELS / "phase311_trajectory_diagnosis_report.json",
        MODELS / "phase316_paired_uplift_report.json",
    ]
    reports = [Report.from_legacy(json.loads(path.read_text())) for path in paths]
    assert {report.phase for report in reports} == {"3.10", "3.11", "3.16"}
    assert all(report.decision for report in reports)
    assert all(report.gates for report in reports)
