from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.sensitivity import (
    CLAIM_BOUNDARY,
    classify_sensitivity,
    fisher_exact_one_sided,
    write_instrument_sensitivity_evidence,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fisher exact one-sided — cross-checked against scipy values in task16 prereg
# ---------------------------------------------------------------------------


def test_fisher_perfect_effect_power_floor() -> None:
    # The pre-registered power floor: a PERFECT effect only clears alpha at n>=4.
    assert abs(fisher_exact_one_sided(0, 3, 3, 3) - 0.05) < 1e-9  # boundary
    assert fisher_exact_one_sided(0, 4, 4, 4) < 0.05
    assert abs(fisher_exact_one_sided(0, 5, 5, 5) - 0.00397) < 1e-4


def test_fisher_no_effect_is_unity() -> None:
    assert fisher_exact_one_sided(0, 5, 0, 5) == 1.0


def test_fisher_monotonic_in_treatment_resolved() -> None:
    ps = [fisher_exact_one_sided(0, 5, k, 5) for k in range(6)]
    assert ps == sorted(ps, reverse=True)  # more flips -> smaller p


# ---------------------------------------------------------------------------
# Classification labels
# ---------------------------------------------------------------------------


def test_classify_detected_at_n5_perfect() -> None:
    res = classify_sensitivity(
        control_outcomes=[False] * 5, treatment_outcomes=[True] * 5
    )
    assert res["label"] == "detected"
    assert res["fisher_one_sided_p"] < 0.05


def test_classify_boundary_n3_is_not_detected() -> None:
    # 3/3 vs 0/3 -> p == 0.05 exactly; conservative strict test must NOT call it detected.
    res = classify_sensitivity(
        control_outcomes=[False] * 3, treatment_outcomes=[True] * 3
    )
    assert res["label"] == "flip_observed_underpowered"


def test_classify_underpowered_partial_flip() -> None:
    res = classify_sensitivity(
        control_outcomes=[False] * 5, treatment_outcomes=[True, False, False, False, False]
    )
    assert res["label"] == "flip_observed_underpowered"


def test_classify_not_detected_zero_flip() -> None:
    res = classify_sensitivity(
        control_outcomes=[False] * 5, treatment_outcomes=[False] * 5
    )
    assert res["label"] == "not_detected"


def test_classify_four_of_five_detected() -> None:
    res = classify_sensitivity(
        control_outcomes=[False] * 5, treatment_outcomes=[True] * 4 + [False]
    )
    assert res["label"] == "detected"


# ---------------------------------------------------------------------------
# Evidence writer — gates + isolation invariants
# ---------------------------------------------------------------------------


def test_evidence_detected_cell(tmp_path: Path) -> None:
    result = write_instrument_sensitivity_evidence(
        tmp_path,
        source_task_id="pallets__flask-4045",
        distillation_level="verbatim",
        control_outcomes=[False] * 5,
        treatment_outcomes=[True] * 5,
    )
    report = result["report"]
    assert report["decision"] == "instrument_sensitivity_detected"
    assert report["contaminated_by_design"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    # Isolation: no uplift wording in the decision name (task16 §6.4).
    assert "uplift" not in report["decision"]
    assert (tmp_path / "instrument_sensitivity_outcome_report.json").is_file()
    assert (tmp_path / "instrument_sensitivity_manifest.json").is_file()


def test_evidence_voids_when_control_not_deterministic_failure(tmp_path: Path) -> None:
    # If a control outcome resolves, the cell cannot demonstrate a fail->resolved
    # flip and must be voided (gate: control_arm_deterministic_failure).
    result = write_instrument_sensitivity_evidence(
        tmp_path,
        source_task_id="x",
        distillation_level="verbatim",
        control_outcomes=[False, True, False],
        treatment_outcomes=[True, True, True],
    )
    assert result["report"]["decision"] == "instrument_sensitivity_cell_void_gate_failure"


def test_evidence_voids_when_control_underpowered(tmp_path: Path) -> None:
    result = write_instrument_sensitivity_evidence(
        tmp_path,
        source_task_id="x",
        distillation_level="verbatim",
        control_outcomes=[False, False],  # n<3
        treatment_outcomes=[True, True],
    )
    assert result["report"]["decision"] == "instrument_sensitivity_cell_void_gate_failure"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_instrument_sensitivity_outcome(tmp_path: Path) -> None:
    # Build two fake treatment eval reports (swebench-style) — one resolved each.
    treat_args: list[str] = []
    for i in range(5):
        rpt = tmp_path / f"treat_{i}.json"
        rpt.write_text(json.dumps({"pallets__flask-4045": {"resolved": True}}))
        treat_args += ["--treatment-eval-report", str(rpt)]
    out = tmp_path / "outcome"
    res = runner.invoke(
        app,
        [
            "instrument-sensitivity-outcome",
            "--source-task-id", "pallets__flask-4045",
            "--distillation-level", "verbatim",
            "--control-outcomes", "0,0,0,0,0",
            "-o", str(out),
            *treat_args,
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["decision"] == "instrument_sensitivity_detected"
    assert payload["contaminated_by_design"] is True
