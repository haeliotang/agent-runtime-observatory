"""Exact (scipy-free) binomial power and exclusion-bound math for paired outcomes.

All probability math uses ``math.comb`` for exact binomial coefficients so the
package keeps zero numerical dependencies. The functions here are pure (no IO)
except for :func:`write_power_report`, which serializes a :class:`PowerReport`
to JSON/manifest artifacts using the shared report helpers.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

POWER_PHASE = "7.protocol_v2_power_analysis"
POWER_VERSION = "phase7_protocol_v2_power_analysis_v1"

# Fixed boundary text: this report never claims an observed effect.
CLAIM_BOUNDARY = (
    "This power report quantifies sample-size requirements and exclusion bounds "
    "for paired intervention outcomes. It does not claim any observed uplift, "
    "predictive diagnosis capability, or generalized causal effect; all completed "
    "pairs to date are no-uplift."
)


def exact_binomial_tail(k: int, n: int, p: float) -> float:
    """Exact lower tail P(X <= k | n, p) for X ~ Binomial(n, p).

    Sums C(n, i) * p^i * (1-p)^(n-i) for i in [0, k] with exact integer
    coefficients. Returns 0.0 when k < 0 and 1.0 when k >= n.
    """
    if n < 0:
        raise ValueError("n must be >= 0")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    q = 1.0 - p
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p**i) * (q ** (n - i))
    return min(1.0, total)


def _upper_tail(k: int, n: int, p: float) -> float:
    """Exact upper tail P(X >= k | n, p) via the complement of the lower tail."""
    if k <= 0:
        return 1.0
    return 1.0 - exact_binomial_tail(k - 1, n, p)


def discordant_pair_test(n_uplift: int, n_harm: int) -> dict[str, Any]:
    """Exact two-sided sign test (McNemar exact) on discordant pairs.

    Among the ``n = n_uplift + n_harm`` discordant pairs, tests whether the
    uplift proportion differs from 0.5. The two-sided p-value doubles the
    smaller exact tail (capped at 1.0). With no discordant pairs the test is
    undefined, so we return p_value=1.0 with an explanatory note.
    """
    n = n_uplift + n_harm
    if n == 0:
        return {
            "n_discordant": 0,
            "n_uplift": n_uplift,
            "n_harm": n_harm,
            "p_value": 1.0,
            "note": "no_discordant_pairs",
        }
    # Smaller tail under H0: p=0.5; two-sided p = min(1, 2 * smaller one-sided tail).
    lower = exact_binomial_tail(min(n_uplift, n_harm), n, 0.5)
    p_value = min(1.0, 2.0 * lower)
    return {
        "n_discordant": n,
        "n_uplift": n_uplift,
        "n_harm": n_harm,
        "p_value": p_value,
        "note": "exact_two_sided_sign_test",
    }


def max_effect_excluded(
    n_pairs_effective: int, n_uplift: int, confidence: float = 0.95
) -> float:
    """Largest per-pair uplift rate excluded at the given confidence.

    Returns the maximum p such that P(X <= n_uplift | n_pairs_effective, p) is
    still >= 1 - confidence. Any per-pair rate above this bound would make the
    observed (small) uplift count implausibly low, so it is "excluded". The
    lower tail is monotonically non-increasing in p, enabling binary search.

    For n_uplift=0, n=7, confidence=0.95 the answer is ~0.348 (since the bound
    solves (1-p)^7 = 0.05).
    """
    if n_pairs_effective <= 0:
        return 1.0
    if n_uplift >= n_pairs_effective:
        return 1.0
    alpha = 1.0 - confidence
    lo, hi = 0.0, 1.0
    # Binary search for the largest p with tail >= alpha. Tail decreases in p,
    # so we move hi down when the tail drops below alpha.
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if exact_binomial_tail(n_uplift, n_pairs_effective, mid) >= alpha:
            lo = mid
        else:
            hi = mid
    return lo


def required_pairs(
    target_uplift_rate: float,
    trigger_hit_rate: float,
    power: float = 0.8,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Effective + total pairs needed to detect a per-pair uplift rate.

    Uses a one-sided exact binomial test of H0: uplift rate = 0 against the
    alternative ``target_uplift_rate``. Under H0 with rate 0, any single uplift
    is decisive (alpha is satisfied trivially for >=1 success), so power reduces
    to detecting at least one uplift: power = 1 - (1 - target)^n. We solve for
    the minimum effective n, then inflate by the trigger-hit rate to get total
    pairs (total = ceil(effective / hit_rate)).
    """
    if not 0.0 < target_uplift_rate <= 1.0:
        raise ValueError("target_uplift_rate must be in (0, 1]")
    if not 0.0 < trigger_hit_rate <= 1.0:
        raise ValueError("trigger_hit_rate must be in (0, 1]")
    if not 0.0 < power < 1.0:
        raise ValueError("power must be in (0, 1)")

    # Minimum effective pairs so that P(>=1 uplift | n, target) >= power.
    n_eff = 1
    while _upper_tail(1, n_eff, target_uplift_rate) < power:
        n_eff += 1
        if n_eff > 100000:  # defensive guard against pathological inputs
            break
    required_total = math.ceil(n_eff / trigger_hit_rate)
    return {
        "required_effective_pairs": n_eff,
        "required_total_pairs": required_total,
        "assumed_trigger_hit_rate": trigger_hit_rate,
        "target_uplift_rate": target_uplift_rate,
        "power": power,
        "alpha": alpha,
    }


def futility_boundary(
    n_pairs_planned: int,
    target_uplift_rate: float,
    alpha_futility: float = 0.1,
) -> dict[str, Any]:
    """Sequential futility boundary over interim checkpoints.

    For each interim k (1..n_pairs_planned), returns the maximum observed uplift
    count at which we would still declare futility for ``target_uplift_rate``.
    Futility is declared when the conditional probability of eventually reaching
    a convincing uplift count is low; we use the simple rule that observing <= v
    uplifts at checkpoint k is futile when, under the target rate, that few
    successes is itself unlikely-to-improve (lower tail <= alpha_futility).
    The boundary is forced monotonically non-decreasing across checkpoints.
    """
    if n_pairs_planned <= 0:
        return {"alpha_futility": alpha_futility, "checkpoints": []}
    checkpoints = []
    prev = 0
    for k in range(1, n_pairs_planned + 1):
        # Largest v such that P(X <= v | k, target) <= alpha_futility.
        boundary = -1
        for v in range(0, k + 1):
            if exact_binomial_tail(v, k, target_uplift_rate) <= alpha_futility:
                boundary = v
            else:
                break
        # Enforce monotonic non-decreasing boundary across checkpoints.
        boundary = max(boundary, prev)
        prev = boundary
        checkpoints.append(
            {"pairs_completed": k, "max_uplift_to_declare_futile": boundary}
        )
    return {"alpha_futility": alpha_futility, "checkpoints": checkpoints}


@dataclass
class PowerReport:
    """Aggregated power / exclusion analysis for paired intervention outcomes."""

    n_pairs: int
    n_uplift: int
    n_harm: int
    trigger_hit_rate: float
    target_uplift_rate: float
    power: float
    alpha: float
    binomial_test_result: dict[str, Any]
    discordant_test_result: dict[str, Any]
    max_effect_excluded_95: float
    required_pairs_result: dict[str, Any]
    futility_result: dict[str, Any]
    minimum_pairs_for_powered_claim: int
    futility_status: str
    claim_boundary: str
    decision: str
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_power_report(
    *,
    n_pairs: int,
    n_uplift: int = 0,
    n_harm: int = 0,
    trigger_hit_rate: float = 0.6,
    target_uplift_rate: float = 0.3,
    power: float = 0.8,
    alpha: float = 0.05,
) -> PowerReport:
    """Assemble a :class:`PowerReport` from the pure analysis primitives."""
    # One-sided exact binomial test of observed uplift count vs the null rate 0
    # is degenerate (any uplift is decisive), so we report the observed-vs-target
    # lower tail as the primary binomial summary instead.
    binomial_test_result = {
        "n_pairs_effective": n_pairs,
        "n_uplift": n_uplift,
        "target_uplift_rate": target_uplift_rate,
        # P(X <= n_uplift | n_pairs, target): how consistent the observed count
        # is with the target rate. Small values argue against the target rate.
        "p_value_observed_le_target": exact_binomial_tail(
            n_uplift, n_pairs, target_uplift_rate
        ),
        "p_value_observed_ge_under_null_zero": 1.0 if n_uplift == 0 else 0.0,
    }
    discordant_test_result = discordant_pair_test(n_uplift, n_harm)
    max_excluded = max_effect_excluded(n_pairs, n_uplift, confidence=0.95)
    required_pairs_result = required_pairs(
        target_uplift_rate=target_uplift_rate,
        trigger_hit_rate=trigger_hit_rate,
        power=power,
        alpha=alpha,
    )
    minimum_pairs = int(required_pairs_result["required_total_pairs"])
    futility_result = futility_boundary(
        n_pairs_planned=max(minimum_pairs, n_pairs),
        target_uplift_rate=target_uplift_rate,
    )
    # Determine futility status against the current completed-pair checkpoint.
    futility_status = "futility_boundary_not_crossed"
    for checkpoint in futility_result["checkpoints"]:
        if checkpoint["pairs_completed"] == n_pairs:
            if n_uplift <= checkpoint["max_uplift_to_declare_futile"]:
                futility_status = "futility_boundary_crossed"
            break

    powered = n_pairs >= minimum_pairs
    if n_uplift == 0 and n_harm == 0:
        decision = "power_analysis_ready_underpowered_for_target_effect"
    elif powered:
        decision = "power_analysis_ready_powered_for_target_effect"
    else:
        decision = "power_analysis_ready_underpowered_for_target_effect"

    summary = {
        "minimum_pairs_for_powered_claim": minimum_pairs,
        "n_pairs_completed": n_pairs,
        "n_uplift": n_uplift,
        "n_harm": n_harm,
        "max_effect_excluded_95": max_excluded,
        "required_effective_pairs": required_pairs_result["required_effective_pairs"],
        "required_total_pairs": minimum_pairs,
        "assumed_trigger_hit_rate": trigger_hit_rate,
        "target_uplift_rate": target_uplift_rate,
        "futility_status": futility_status,
        "powered_for_target_effect": powered,
    }

    return PowerReport(
        n_pairs=n_pairs,
        n_uplift=n_uplift,
        n_harm=n_harm,
        trigger_hit_rate=trigger_hit_rate,
        target_uplift_rate=target_uplift_rate,
        power=power,
        alpha=alpha,
        binomial_test_result=binomial_test_result,
        discordant_test_result=discordant_test_result,
        max_effect_excluded_95=max_excluded,
        required_pairs_result=required_pairs_result,
        futility_result=futility_result,
        minimum_pairs_for_powered_claim=minimum_pairs,
        futility_status=futility_status,
        claim_boundary=CLAIM_BOUNDARY,
        decision=decision,
        summary=summary,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": None,
        "exists": path.is_file(),
    }


def write_power_report(
    output_dir: Path,
    *,
    n_pairs: int,
    n_uplift: int = 0,
    n_harm: int = 0,
    trigger_hit_rate: float = 0.6,
    target_uplift_rate: float = 0.3,
    power: float = 0.8,
    alpha: float = 0.05,
    batch_outcomes_report: Path | None = None,
) -> dict[str, Any]:
    """Build and serialize the power report + manifest into ``output_dir``.

    Writes ``protocol_v2_power_report.json`` and
    ``protocol_v2_power_manifest.json`` and returns the in-memory payloads and
    artifact paths for callers (CLI / tests).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    power_report = build_power_report(
        n_pairs=n_pairs,
        n_uplift=n_uplift,
        n_harm=n_harm,
        trigger_hit_rate=trigger_hit_rate,
        target_uplift_rate=target_uplift_rate,
        power=power,
        alpha=alpha,
    )
    report = generate_report(
        phase=POWER_PHASE,
        decision=power_report.decision,
        gate_results={
            "binomial_math_exact_no_scipy": True,
            "claim_boundary_present": bool(power_report.claim_boundary),
            "no_observed_uplift_claimed": power_report.n_uplift == 0,
            "minimum_pairs_recorded": power_report.minimum_pairs_for_powered_claim > 0,
        },
        extras={
            "version": POWER_VERSION,
            "claim_boundary": power_report.claim_boundary,
            "power_report": power_report.to_dict(),
            "summary": power_report.summary,
        },
    )
    report_path = output_dir / "protocol_v2_power_report.json"
    manifest_path = output_dir / "protocol_v2_power_manifest.json"
    _write_json(report_path, report)

    artifacts = [_artifact(report_path)]
    if batch_outcomes_report is not None and Path(batch_outcomes_report).is_file():
        artifacts.append(_artifact(Path(batch_outcomes_report)))
    manifest = generate_manifest(
        phase=POWER_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = POWER_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "power_report": power_report,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "CLAIM_BOUNDARY",
    "POWER_VERSION",
    "PowerReport",
    "build_power_report",
    "discordant_pair_test",
    "exact_binomial_tail",
    "futility_boundary",
    "max_effect_excluded",
    "required_pairs",
    "write_power_report",
]
