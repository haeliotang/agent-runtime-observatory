from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.epsilon import (
    CLAIM_BOUNDARY,
    flip_rate_estimate,
    required_pairs_with_noise,
    scan_rerun_outcomes,
    wilson_interval,
    write_epsilon_evidence,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------


def test_wilson_interval_wide_at_n6_all_stable() -> None:
    # n=6, all stable (0 flips): even so, cannot exclude 30% flip rate at 95% CI.
    _lower, upper = wilson_interval(0, 6)
    assert upper > 0.3, (
        f"Wilson upper {upper:.3f} should exceed 0.30 for n=6 all-stable "
        "(can't rule out 30% noise floor with only 6 reruns)"
    )


def test_wilson_interval_all_flip() -> None:
    lower, upper = wilson_interval(6, 6)
    assert lower > 0.5
    assert upper >= 1.0 - 1e-9


def test_wilson_interval_half_flip() -> None:
    lower, upper = wilson_interval(3, 6)
    assert lower < 0.5 < upper


def test_wilson_interval_degenerate_n0() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_symmetry() -> None:
    lo_a, hi_a = wilson_interval(2, 10)
    lo_b, hi_b = wilson_interval(8, 10)
    # complementary proportions produce mirror intervals
    assert abs((1.0 - hi_b) - lo_a) < 1e-9
    assert abs((1.0 - lo_b) - hi_a) < 1e-9


# ---------------------------------------------------------------------------
# flip_rate_estimate
# ---------------------------------------------------------------------------


def test_flip_rate_all_stable() -> None:
    est = flip_rate_estimate([False, False, False], reference_outcome=False)
    assert est["flip_count"] == 0
    assert est["rerun_count"] == 3
    assert est["point_estimate"] == 0.0
    assert est["wilson_upper_95"] > 0.0  # not zero even with no flips


def test_flip_rate_all_flipped() -> None:
    est = flip_rate_estimate([True, True], reference_outcome=False)
    assert est["flip_count"] == 2
    assert est["point_estimate"] == 1.0


def test_flip_rate_mixed() -> None:
    est = flip_rate_estimate([False, True, False], reference_outcome=False)
    assert est["flip_count"] == 1
    assert abs(est["point_estimate"] - 1 / 3) < 1e-9


def test_flip_rate_empty() -> None:
    est = flip_rate_estimate([])
    assert est["rerun_count"] == 0
    assert est["point_estimate"] is None
    assert est["wilson_upper_95"] is None


# ---------------------------------------------------------------------------
# required_pairs_with_noise
# ---------------------------------------------------------------------------


def test_required_pairs_below_noise_floor() -> None:
    result = required_pairs_with_noise(0.05, 1.0, epsilon=0.10)
    assert result["decision"] == "target_below_noise_floor_unmeasurable"
    assert result["required_effective_pairs"] is None
    assert result["required_total_pairs"] is None
    assert result["epsilon"] == 0.10
    assert result["target_uplift_rate"] == 0.05


def test_required_pairs_at_exact_floor_unmeasurable() -> None:
    result = required_pairs_with_noise(0.10, 1.0, epsilon=0.10)
    assert result["decision"] == "target_below_noise_floor_unmeasurable"


def test_required_pairs_zero_epsilon_degrades_to_base() -> None:
    from wutai_clinic.engine.power import required_pairs

    base = required_pairs(target_uplift_rate=0.3, trigger_hit_rate=1.0)
    noise_adj = required_pairs_with_noise(0.3, 1.0, epsilon=0.0)
    assert noise_adj["decision"] == "required_pairs_noise_adjusted_ready"
    assert noise_adj["required_effective_pairs"] == base["required_effective_pairs"]


def test_required_pairs_nonzero_epsilon_increases_required() -> None:
    base = required_pairs_with_noise(0.3, 1.0, epsilon=0.0)
    noisy = required_pairs_with_noise(0.3, 1.0, epsilon=0.05)
    assert noisy["required_effective_pairs"] >= base["required_effective_pairs"]


# ---------------------------------------------------------------------------
# scan_rerun_outcomes
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_scan_rerun_outcomes(tmp_path: Path) -> None:
    instance = "pkg__repo-1"
    rerun_root = tmp_path / "epsilon_rerun" / instance
    for run_idx, resolved in enumerate([False, False, True], 1):
        _write_json(
            rerun_root / f"run_{run_idx}" / instance / "report.json",
            {instance: {"resolved": resolved}},
        )
    outcomes = scan_rerun_outcomes(rerun_root, instance)
    assert outcomes == [False, False, True]


def test_scan_rerun_outcomes_missing_root(tmp_path: Path) -> None:
    assert scan_rerun_outcomes(tmp_path / "missing", "any") == []


def test_scan_rerun_outcomes_wrong_instance_skipped(tmp_path: Path) -> None:
    instance = "pkg__repo-1"
    _write_json(
        tmp_path / "run_1" / instance / "report.json",
        {"other__instance": {"resolved": True}},
    )
    outcomes = scan_rerun_outcomes(tmp_path, instance)
    assert outcomes == []


# ---------------------------------------------------------------------------
# write_epsilon_evidence
# ---------------------------------------------------------------------------


def test_write_epsilon_evidence_full(tmp_path: Path) -> None:
    estimates = {
        "flask-4045": {"rerun_count": 3, "flip_count": 0, "reference_outcome": False,
                       "point_estimate": 0.0, "wilson_lower_95": 0.0,
                       "wilson_upper_95": 0.562, "confidence": 0.95},
    }
    result = write_epsilon_evidence(tmp_path / "out", estimates=estimates)
    report = result["report"]
    assert report["decision"] == "epsilon_noise_floor_estimated"
    assert report["passed"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert "pooled_estimate" in report
    assert "per_instance_estimates" in report
    assert (tmp_path / "out" / "epsilon_report.json").is_file()
    assert (tmp_path / "out" / "epsilon_manifest.json").is_file()


def test_write_epsilon_evidence_no_outcomes_blocked(tmp_path: Path) -> None:
    result = write_epsilon_evidence(tmp_path / "out", estimates={})
    assert result["report"]["decision"] == "epsilon_blocked_no_rerun_outcomes"
    assert result["report"]["passed"] is False


def test_write_epsilon_evidence_target_below_floor_annotated(tmp_path: Path) -> None:
    # point_estimate = 0.0 from 3 all-stable reruns → target 0.05 is not below floor (eps=0)
    estimates = {
        "inst": {"rerun_count": 3, "flip_count": 0, "reference_outcome": False,
                 "point_estimate": 0.0, "wilson_lower_95": 0.0,
                 "wilson_upper_95": 0.562, "confidence": 0.95},
    }
    result = write_epsilon_evidence(
        tmp_path / "out",
        estimates=estimates,
        target_uplift_rate=0.05,
    )
    consumption = result["report"]["recommended_consumption"]["required_pairs_with_noise_at_point"]
    # epsilon=0 so target is not below floor
    assert consumption is not None
    assert consumption["decision"] == "required_pairs_noise_adjusted_ready"


# ---------------------------------------------------------------------------
# CLI epsilon-estimate
# ---------------------------------------------------------------------------


def test_cli_epsilon_estimate_manual_outcomes(tmp_path: Path) -> None:
    out = tmp_path / "eps_out"
    result = runner.invoke(
        app,
        [
            "epsilon-estimate",
            "--outcomes", "0,0,0",
            "--instance-id", "pkg__repo-1",
            "--output-dir", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "epsilon_noise_floor_estimated"
    assert payload["pooled_estimate"]["flip_count"] == 0
    assert (out / "epsilon_report.json").is_file()
    assert (out / "epsilon_manifest.json").is_file()


def test_cli_epsilon_estimate_rerun_root(tmp_path: Path) -> None:
    instance = "pkg__repo-2"
    rerun_root = tmp_path / "reruns" / instance
    for i, resolved in enumerate([False, True], 1):
        _write_json(
            rerun_root / f"run_{i}" / instance / "report.json",
            {instance: {"resolved": resolved}},
        )
    out = tmp_path / "eps_out"
    result = runner.invoke(
        app,
        [
            "epsilon-estimate",
            "--rerun-root", str(rerun_root),
            "--instance-id", instance,
            "--output-dir", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["per_instance_estimates"][instance]["flip_count"] == 1


def test_cli_epsilon_rerun_root_requires_instance_id(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "epsilon-estimate",
            "--rerun-root", str(tmp_path),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
