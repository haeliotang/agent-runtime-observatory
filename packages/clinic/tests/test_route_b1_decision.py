from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.route_b1_decision import (
    aggregate_cells_to_anchor_outcomes,
    anchor_verdict,
    route_b1_decision,
)

runner = CliRunner()


def _clean_treatment(reps, resolved):
    """k treatment reps, all M-clean (injected once + leak clean + trigger hit)."""
    return {
        "treatment_resolved": list(resolved),
        "treatment_injected_once": [True] * reps,
        "treatment_leak_clean": [True] * reps,
        "treatment_trigger_hit": [True] * reps,
    }


def _det_fail_control(reps):
    return {"control_resolved": [False] * reps, "control_injection_zero": [True] * reps}


# --- anchor verdict ------------------------------------------------------------
def test_verdict_signal_when_treatment_resolves_and_control_det_fails() -> None:
    v = anchor_verdict(anchor="a", **_det_fail_control(5), **_clean_treatment(5, [False, False, True, False, False]))
    assert v["verdict"] == "signal_of_life"
    assert v["counted"] is True


def test_verdict_no_uplift_when_both_fail() -> None:
    v = anchor_verdict(anchor="a", **_det_fail_control(5), **_clean_treatment(5, [False] * 5))
    assert v["verdict"] == "no_uplift"
    assert v["counted"] is True


def test_verdict_not_counted_when_control_not_deterministic_fail() -> None:
    ctrl = {"control_resolved": [False, True, False, False, False], "control_injection_zero": [True] * 5}
    v = anchor_verdict(anchor="a", **ctrl, **_clean_treatment(5, [True] * 5))
    assert v["verdict"] == "anchor_control_not_deterministic_fail"
    assert v["counted"] is False


def test_verdict_not_counted_on_all_trigger_miss_or_leak() -> None:
    t = {
        "treatment_resolved": [True] * 5,  # would "resolve" but reps are invalid
        "treatment_injected_once": [True] * 5,
        "treatment_leak_clean": [False] * 5,  # all leak -> no valid reps
        "treatment_trigger_hit": [True] * 5,
    }
    v = anchor_verdict(anchor="a", **_det_fail_control(5), **t)
    assert v["counted"] is False
    assert v["verdict"] == "trigger_miss_or_void_not_counted"


# --- aggregate decision --------------------------------------------------------
def test_decision_signal_of_life_if_any_anchor_signals() -> None:
    outcomes = [
        {"anchor": "a", **_det_fail_control(5), **_clean_treatment(5, [False] * 5)},
        {"anchor": "b", **_det_fail_control(5), **_clean_treatment(5, [False, True, False, False, False])},
    ]
    r = route_b1_decision(outcomes)
    assert r["decision"] == "route_b1_probe_signal_of_life"
    assert r["signal_anchors"] == ["b"]
    assert r["gates"]["b6_red_line_unchanged"] is True
    assert "uplift" not in r["decision"]


def test_decision_futility_when_all_counted_no_uplift() -> None:
    outcomes = [
        {"anchor": a, **_det_fail_control(5), **_clean_treatment(5, [False] * 5)} for a in ("a", "b", "c", "d")
    ]
    r = route_b1_decision(outcomes)
    assert r["decision"] == "route_b1_probe_futility_null"
    assert r["counted_anchor_count"] == 4


def test_decision_inconclusive_when_nothing_counts() -> None:
    # all anchors trigger-miss (no valid treatment reps)
    t_miss = {
        "treatment_resolved": [False] * 5,
        "treatment_injected_once": [True] * 5,
        "treatment_leak_clean": [True] * 5,
        "treatment_trigger_hit": [False] * 5,
    }
    outcomes = [{"anchor": a, **_det_fail_control(5), **t_miss} for a in ("a", "b")]
    r = route_b1_decision(outcomes)
    assert r["decision"] == "route_b1_probe_inconclusive_recalibrate"
    assert r["counted_anchor_count"] == 0


# --- CLI + cell aggregation ----------------------------------------------------
def test_aggregate_cells_round_trip() -> None:
    cells = [
        {"anchor": "a", "arm": "control", "resolved": False, "injection_count": 0},
        {"anchor": "a", "arm": "treatment", "resolved": True, "injected_once": True, "leak_clean": True, "trigger_hit": True},
    ]
    out = aggregate_cells_to_anchor_outcomes(cells)
    assert out[0]["anchor"] == "a"
    assert out[0]["control_resolved"] == [False]
    assert out[0]["treatment_resolved"] == [True]


def test_cli_decision(tmp_path: Path) -> None:
    cells = tmp_path / "cells.jsonl"
    lines = []
    for anchor in ("a", "b"):
        for _ in range(5):
            lines.append({"anchor": anchor, "arm": "control", "resolved": False, "injection_count": 0})
        for i in range(5):
            res = anchor == "b" and i == 0  # one treatment resolve on anchor b
            lines.append({"anchor": anchor, "arm": "treatment", "resolved": res, "injected_once": True, "leak_clean": True, "trigger_hit": True})
    cells.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    res = runner.invoke(app, ["route-b1-decision", str(cells), "-o", str(tmp_path / "dec")])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["decision"] == "route_b1_probe_signal_of_life"
    assert out["signal_anchors"] == ["b"]
