from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.engine.power import (
    CLAIM_BOUNDARY,
    build_power_report,
    discordant_pair_test,
    exact_binomial_tail,
    futility_boundary,
    max_effect_excluded,
    required_pairs,
    write_power_report,
)


def test_exact_binomial_tail_known_value() -> None:
    # P(X <= 0 | n=7, p=0.3) == 0.7 ** 7 == 0.0823543
    assert abs(exact_binomial_tail(0, 7, 0.3) - 0.0823543) < 1e-4


def test_exact_binomial_tail_edges() -> None:
    assert exact_binomial_tail(-1, 7, 0.3) == 0.0
    assert exact_binomial_tail(7, 7, 0.3) == 1.0
    # Full sum across all i equals 1.
    assert abs(exact_binomial_tail(5, 5, 0.4) - 1.0) < 1e-12


def test_max_effect_excluded_known_value() -> None:
    # Solves (1-p)^7 = 0.05 -> p ~= 0.348.
    assert abs(max_effect_excluded(7, 0, confidence=0.95) - 0.348) < 1e-3


def test_max_effect_excluded_monotone_in_uplift() -> None:
    # Observing more uplift cannot tighten (lower) the excluded bound.
    bound0 = max_effect_excluded(7, 0)
    bound1 = max_effect_excluded(7, 1)
    assert bound1 > bound0


def test_required_pairs_doubling_relation() -> None:
    half = required_pairs(target_uplift_rate=0.3, trigger_hit_rate=0.5)
    full = required_pairs(target_uplift_rate=0.3, trigger_hit_rate=1.0)
    assert half["required_effective_pairs"] == full["required_effective_pairs"]
    assert half["required_total_pairs"] == 2 * full["required_total_pairs"]


def test_discordant_pair_test_no_pairs() -> None:
    result = discordant_pair_test(0, 0)
    assert result["p_value"] == 1.0
    assert result["note"] == "no_discordant_pairs"


def test_discordant_pair_test_symmetric() -> None:
    # All discordant pairs uplift => strongly significant two-sided sign test.
    result = discordant_pair_test(7, 0)
    assert result["n_discordant"] == 7
    assert result["p_value"] < 0.05


def test_futility_boundary_non_decreasing() -> None:
    boundary = futility_boundary(10, target_uplift_rate=0.3)
    values = [c["max_uplift_to_declare_futile"] for c in boundary["checkpoints"]]
    assert len(values) == 10
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_build_power_report_no_uplift_decision() -> None:
    report = build_power_report(n_pairs=7, n_uplift=0, n_harm=0)
    assert report.decision == "power_analysis_ready_underpowered_for_target_effect"
    assert report.claim_boundary == CLAIM_BOUNDARY
    assert report.summary["minimum_pairs_for_powered_claim"] > 0
    assert abs(report.max_effect_excluded_95 - 0.348) < 1e-3


def test_write_power_report_creates_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "power"
    result = write_power_report(
        output_dir,
        n_pairs=7,
        n_uplift=0,
        n_harm=0,
        trigger_hit_rate=0.6,
        target_uplift_rate=0.3,
    )
    report_path = output_dir / "protocol_v2_power_report.json"
    manifest_path = output_dir / "protocol_v2_power_manifest.json"
    assert report_path.is_file()
    assert manifest_path.is_file()
    assert result["report_path"] == report_path
    assert result["manifest_path"] == manifest_path

    report = json.loads(report_path.read_text())
    assert report["decision"] == "power_analysis_ready_underpowered_for_target_effect"
    assert report["passed"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert report["summary"]["minimum_pairs_for_powered_claim"] > 0

    manifest = json.loads(manifest_path.read_text())
    assert manifest["decision"] == report["decision"]
    assert any(
        entry["path"].endswith("protocol_v2_power_report.json") for entry in manifest["artifacts"]
    )
