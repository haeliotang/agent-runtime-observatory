from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.route_b1_cells import (
    assemble_cells,
    cell_from_arm_report,
    rep_index_from_name,
    resolved_map_from_labels,
)

runner = CliRunner()


def test_rep_index_parsing() -> None:
    assert rep_index_from_name("rep_3") == 3
    assert rep_index_from_name("rep-7") == 7
    assert rep_index_from_name("nope") == 0


def test_cell_from_treatment_report_derives_mchecks() -> None:
    report = {
        "arm_type": "treatment",
        "source_task_id": "a",
        "injection_count": 1,
        "m2b_leak_findings": [],
        "m2b_capture_leak_findings": [],
    }
    cell = cell_from_arm_report(report, rep=2, resolved=True)
    assert cell == {
        "anchor": "a",
        "arm": "treatment",
        "rep": 2,
        "resolved": True,
        "injection_count": 1,
        "run_ok": True,
        "injected_once": True,
        "leak_clean": True,
        "trigger_hit": True,
    }


def test_cell_treatment_leak_marks_unclean() -> None:
    report = {
        "arm_type": "treatment",
        "source_task_id": "a",
        "injection_count": 0,
        "m2b_capture_leak_findings": ["fail_to_pass_node_in_payload:x"],
    }
    cell = cell_from_arm_report(report, rep=1, resolved=False)
    assert cell["leak_clean"] is False
    assert cell["trigger_hit"] is False  # injection_count 0


def test_cell_control_report_is_minimal() -> None:
    report = {"arm_type": "control", "source_task_id": "a", "injection_count": 0}
    cell = cell_from_arm_report(report, rep=1, resolved=False)
    assert "injected_once" not in cell
    assert cell["injection_count"] == 0


def test_cell_carries_run_ok_from_report() -> None:
    crashed = {
        "arm_type": "control",
        "source_task_id": "a",
        "injection_count": 0,
        "run_exit_ok": False,
    }
    assert cell_from_arm_report(crashed, rep=1, resolved=False)["run_ok"] is False
    clean = {
        "arm_type": "control",
        "source_task_id": "a",
        "injection_count": 0,
        "run_exit_ok": True,
    }
    assert cell_from_arm_report(clean, rep=1, resolved=False)["run_ok"] is True


def test_crashed_cells_excluded_from_decision() -> None:
    from wutai_clinic.intervention.route_b1_decision import (
        aggregate_cells_to_anchor_outcomes,
        route_b1_decision,
    )

    # anchor a: all control reps crashed (run_ok False) -> no valid control data ->
    # must NOT count as a deterministic-fail anchor.
    cells = []
    for rep in range(1, 6):
        cells.append(
            {
                "anchor": "a",
                "arm": "control",
                "resolved": False,
                "injection_count": 0,
                "run_ok": False,
            }
        )
        cells.append(
            {
                "anchor": "a",
                "arm": "treatment",
                "resolved": False,
                "injection_count": 1,
                "injected_once": True,
                "leak_clean": True,
                "trigger_hit": True,
                "run_ok": False,
            }
        )
    outcomes = aggregate_cells_to_anchor_outcomes(cells)
    r = route_b1_decision(outcomes)
    # nothing valid counts -> inconclusive, NOT a false futility_null
    assert r["decision"] == "route_b1_probe_inconclusive_recalibrate"
    assert r["counted_anchor_count"] == 0


def test_assemble_flags_missing_resolved_label() -> None:
    reports = [({"arm_type": "control", "source_task_id": "a", "injection_count": 0}, 1)]
    out = assemble_cells(reports, resolved_map_from_labels([]))  # no labels
    assert out["complete"] is False
    assert out["incomplete_count"] == 1
    assert out["cell_count"] == 0


def test_cli_cells_then_feeds_decision(tmp_path: Path) -> None:
    # build a tiny arms tree: anchor a, control+treatment, rep_1; treatment resolves
    def _arm(anchor, arm, rep, inj):
        d = tmp_path / "arms" / anchor / arm / f"rep_{rep}"
        d.mkdir(parents=True)
        (d / "b1_live_arm_report.json").write_text(
            json.dumps(
                {
                    "arm_type": arm,
                    "source_task_id": anchor,
                    "injection_count": inj,
                    "m2b_leak_findings": [],
                    "m2b_capture_leak_findings": [],
                }
            )
        )

    _arm("a", "control", 1, 0)
    _arm("a", "treatment", 1, 1)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"anchor": "a", "arm": "control", "rep": 1, "resolved": False})
        + "\n"
        + json.dumps({"anchor": "a", "arm": "treatment", "rep": 1, "resolved": True})
        + "\n"
    )
    out_dir = tmp_path / "cells"
    res = runner.invoke(
        app,
        [
            "route-b1-cells",
            str(tmp_path / "arms"),
            "--resolved-labels",
            str(labels),
            "-o",
            str(out_dir),
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["complete"] is True
    assert out["cell_count"] == 2

    # the assembled cells must drive route-b1-decision end to end
    dec = runner.invoke(
        app, ["route-b1-decision", str(out_dir / "b1_cells.jsonl"), "-o", str(tmp_path / "dec")]
    )
    assert dec.exit_code == 0, dec.output
    assert json.loads(dec.output)["decision"] == "route_b1_probe_signal_of_life"
