"""Nondeterminism noise-floor (epsilon) estimation from pure same-config reruns.

Epsilon is the outcome-flip rate of the execution substrate (agent + provider +
container) when the same instance is rerun with a byte-identical configuration
and NO intervention hook. Any per-pair uplift target at or below epsilon is
unmeasurable in principle; :func:`required_pairs_with_noise` folds the floor
into the existing power calculation.

Reruns are control-arm only: this module measures environment noise, never
intervention effects, and its outputs support no arm-vs-arm comparison.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from wutai_clinic.engine.power import required_pairs
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

EPSILON_PHASE = "7.epsilon_noise_floor"
EPSILON_VERSION = "phase7_epsilon_noise_floor_v1"

CLAIM_BOUNDARY = (
    "Epsilon quantifies outcome-measurement noise of the execution substrate. "
    "It supports no claim about intervention effects."
)


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (robust at small n)."""
    if n <= 0:
        return (0.0, 1.0)
    # Two-sided z for the requested confidence (0.95 -> 1.959964...).
    z = _normal_quantile(0.5 + confidence / 2.0)
    p_hat = successes / n
    denom = 1.0 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def _normal_quantile(p: float) -> float:
    """Standard normal quantile via Acklam's rational approximation (stdlib-only)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    # Coefficients for the central region |p - 0.5| <= 0.425.
    a = (-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239)
    b = (-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572)
    c = (-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783)
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416)
    p_low = 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 1 - p_low:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def flip_rate_estimate(
    outcomes: list[bool],
    *,
    reference_outcome: bool = False,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Flip rate of rerun outcomes relative to the original (reference) outcome."""
    n = len(outcomes)
    flips = sum(1 for outcome in outcomes if outcome != reference_outcome)
    lower, upper = wilson_interval(flips, n, confidence)
    return {
        "rerun_count": n,
        "flip_count": flips,
        "reference_outcome": reference_outcome,
        "point_estimate": (flips / n) if n else None,
        "wilson_lower_95": lower if n else None,
        "wilson_upper_95": upper if n else None,
        "confidence": confidence,
    }


def effective_uplift_floor(epsilon: float) -> float:
    """Smallest per-pair uplift rate measurable above the substrate noise floor."""
    if not 0.0 <= epsilon <= 1.0:
        raise ValueError("epsilon must be in [0, 1]")
    return epsilon


def required_pairs_with_noise(
    target_uplift_rate: float,
    trigger_hit_rate: float,
    *,
    epsilon: float,
    power: float = 0.8,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Noise-adjusted required pairs: the detectable signal is target minus epsilon."""
    floor = effective_uplift_floor(epsilon)
    if target_uplift_rate <= floor:
        return {
            "decision": "target_below_noise_floor_unmeasurable",
            "target_uplift_rate": target_uplift_rate,
            "epsilon": epsilon,
            "effective_uplift_floor": floor,
            "required_effective_pairs": None,
            "required_total_pairs": None,
        }
    adjusted_target = target_uplift_rate - epsilon
    base = required_pairs(
        target_uplift_rate=adjusted_target,
        trigger_hit_rate=trigger_hit_rate,
        power=power,
        alpha=alpha,
    )
    return {
        "decision": "required_pairs_noise_adjusted_ready",
        "target_uplift_rate": target_uplift_rate,
        "epsilon": epsilon,
        "effective_uplift_floor": floor,
        "noise_adjusted_target": adjusted_target,
        **{k: v for k, v in base.items()},
    }


# ---------------------------------------------------------------------------
# Rerun config cloning + outcome scanning
# ---------------------------------------------------------------------------


def clone_runtime_config_for_rerun(
    config_path: Path,
    rerun_output_dir: Path,
) -> dict[str, Any]:
    """Byte-faithful clone of a preflight runtime config with output redirected.

    Redirection is mandatory: rerunning with the original baked-in output_dir
    would overwrite the completed pair's native artifacts under models/.
    """
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["output_dir"] = rerun_output_dir.as_posix()
    clinic = dict(payload.get("wutai_clinic") or {})
    clinic["epsilon_rerun"] = {
        "source_config": config_path.as_posix(),
        "purpose": "substrate_noise_floor_estimation_control_arm_only",
    }
    payload["wutai_clinic"] = clinic
    return payload


def scan_rerun_outcomes(rerun_root: Path, instance_id: str) -> list[bool]:
    """Collect resolved flags from swebench-style report.json files under run_* dirs."""
    outcomes: list[bool] = []
    if not rerun_root.is_dir():
        return outcomes
    for report_path in sorted(rerun_root.glob("run_*/**/report.json")):
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        instance = payload.get(instance_id)
        if isinstance(instance, dict) and "resolved" in instance:
            outcomes.append(bool(instance["resolved"]))
    return outcomes


def write_epsilon_evidence(
    output_dir: Path,
    *,
    estimates: dict[str, dict[str, Any]],
    existing_rerun_inventory: list[dict[str, Any]] | None = None,
    target_uplift_rate: float = 0.2,
    trigger_hit_rate: float = 1.0,
) -> dict[str, Any]:
    """Write epsilon_report.json + manifest from per-instance flip-rate estimates."""
    output_dir.mkdir(parents=True, exist_ok=True)
    all_outcome_flips = sum(e["flip_count"] for e in estimates.values())
    all_reruns = sum(e["rerun_count"] for e in estimates.values())
    pooled = flip_rate_estimate(
        [True] * all_outcome_flips + [False] * (all_reruns - all_outcome_flips),
        reference_outcome=False,
    )
    epsilon_point = pooled["point_estimate"] if all_reruns else None
    consumption = (
        required_pairs_with_noise(
            target_uplift_rate, trigger_hit_rate, epsilon=float(epsilon_point)
        )
        if epsilon_point is not None
        else None
    )
    gates = {
        "epsilon_rerun_outcomes_present": all_reruns > 0,
        "control_arm_only_semantics": True,
    }
    decision = (
        "epsilon_noise_floor_estimated"
        if all_reruns > 0
        else "epsilon_blocked_no_rerun_outcomes"
    )
    report = generate_report(
        phase=EPSILON_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": EPSILON_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "per_instance_estimates": estimates,
            "pooled_estimate": pooled if all_reruns else None,
            "existing_rerun_inventory": list(existing_rerun_inventory or []),
            "recommended_consumption": {
                "note": (
                    "Feed pooled point/upper estimates into power-analysis via "
                    "required_pairs_with_noise before authorizing new pairs."
                ),
                "required_pairs_with_noise_at_point": consumption,
            },
        },
    )
    report_path = output_dir / "epsilon_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    manifest = generate_manifest(
        phase=EPSILON_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": None,
            }
        ],
    )
    manifest_path = output_dir / "epsilon_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}
