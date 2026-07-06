"""Instrument sensitivity (positive control) analysis — uplift direction.

Every negative conclusion in the registry (no-uplift, no predictive validity,
no cognition benefit) rests on an untested assumption: that the instrument can
detect an uplift-direction outcome flip at all. Harm-direction sensitivity is
demonstrated (post_repair_outcomes + observe-only attribution); uplift-direction
sensitivity has never been calibrated.

This module analyzes a deliberate positive control: oracle-distilled hints
injected on instances whose control arm fails deterministically. Outcomes here
are contaminated by design (oracle semantics, same as the v2_oracle_probe
layer) and support exactly one claim family — "the instrument can/cannot detect
a known-strong uplift-direction effect". They never enter uplift, harm, or any
intervention-effectiveness statistic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SENSITIVITY_PHASE = "9.instrument_sensitivity_positive_control"
SENSITIVITY_VERSION = "wave5_instrument_sensitivity_v1"
INSTRUMENT_SENSITIVITY_LAYER = "instrument_sensitivity_v1"

CLAIM_BOUNDARY = (
    "Positive-control arms are contaminated by design (oracle-derived hints) "
    "and exist solely to calibrate uplift-direction detection sensitivity of "
    "the measurement instrument. No outcome from this layer supports any "
    "intervention-effectiveness, harm, or improvement claim."
)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_one_sided(
    control_resolved: int,
    control_total: int,
    treatment_resolved: int,
    treatment_total: int,
) -> float:
    """One-sided Fisher exact p-value (treatment resolves MORE than control).

    Hypergeometric upper tail on the 2x2 table with margins fixed: probability,
    under H0 (no association), of seeing at least `treatment_resolved` resolved
    outcomes in the treatment column.
    """
    if min(control_total, treatment_total) < 0 or treatment_total == 0:
        raise ValueError("arm totals must be positive")
    if not (0 <= control_resolved <= control_total):
        raise ValueError("control_resolved out of range")
    if not (0 <= treatment_resolved <= treatment_total):
        raise ValueError("treatment_resolved out of range")
    n = control_total + treatment_total
    resolved_margin = control_resolved + treatment_resolved
    # Sum hypergeometric PMF over tables at least as extreme (>= observed
    # treatment_resolved), respecting margin bounds.
    lo = max(0, resolved_margin - control_total)
    hi = min(treatment_total, resolved_margin)
    log_denom = _log_comb(n, resolved_margin)
    p = 0.0
    for k in range(treatment_resolved, hi + 1):
        if k < lo:
            continue
        log_p = (
            _log_comb(treatment_total, k)
            + _log_comb(control_total, resolved_margin - k)
            - log_denom
        )
        p += math.exp(log_p)
    return min(1.0, p)


def classify_sensitivity(
    *,
    control_outcomes: list[bool],
    treatment_outcomes: list[bool],
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Pre-registered classification of one positive-control cell (one target).

    detected           — treatment flips are statistically distinguishable from
                         the control noise floor at `alpha` (Fisher exact,
                         one-sided).
    flip_observed_underpowered — at least one fail->resolved flip, but the cell
                         is too small to clear `alpha`; sensitivity is bounded,
                         not established.
    not_detected       — zero treatment flips; at this dose the instrument
                         registered nothing.
    """
    control_resolved = sum(1 for o in control_outcomes if o)
    treatment_resolved = sum(1 for o in treatment_outcomes if o)
    p_value = fisher_exact_one_sided(
        control_resolved,
        len(control_outcomes),
        treatment_resolved,
        len(treatment_outcomes),
    )
    # Strict significance with a tolerance so the exact-boundary case (e.g.
    # 3/3 vs 0/3 -> p == 0.05) is deterministically NOT "detected" regardless
    # of floating-point rounding in the lgamma/exp evaluation. This is the
    # conservative convention (p < alpha) and matches the power floor noted in
    # the wave5 task16 pre-registration: balanced n>=4 per arm is required for
    # a perfect effect to clear alpha=0.05 (1/C(2n,n): n=3 -> 0.05, n=4 -> 0.014).
    if treatment_resolved > 0 and p_value < alpha - 1e-9:
        label = "detected"
    elif treatment_resolved > 0:
        label = "flip_observed_underpowered"
    else:
        label = "not_detected"
    return {
        "control_resolved": control_resolved,
        "control_total": len(control_outcomes),
        "treatment_resolved": treatment_resolved,
        "treatment_total": len(treatment_outcomes),
        "fisher_one_sided_p": p_value,
        "alpha": alpha,
        "label": label,
    }


def write_instrument_sensitivity_evidence(
    output_dir: Path,
    *,
    source_task_id: str,
    distillation_level: str,
    control_outcomes: list[bool],
    treatment_outcomes: list[bool],
    alpha: float = 0.05,
    control_lineage_note: str = "",
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write instrument_sensitivity_outcome_report.json + manifest for one cell.

    Gates encode the pre-registration: the control arm must be a deterministic
    failure (every control outcome unresolved) — otherwise the cell cannot
    demonstrate a fail->resolved flip and is void for sensitivity purposes.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    classification = classify_sensitivity(
        control_outcomes=control_outcomes,
        treatment_outcomes=treatment_outcomes,
        alpha=alpha,
    )
    gates = {
        "control_arm_deterministic_failure": not any(control_outcomes),
        "control_rerun_count_at_least_3": len(control_outcomes) >= 3,
        "treatment_outcomes_present": len(treatment_outcomes) > 0,
    }
    if all(gates.values()):
        decision = f"instrument_sensitivity_{classification['label']}"
    else:
        decision = "instrument_sensitivity_cell_void_gate_failure"
    report = generate_report(
        phase=SENSITIVITY_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SENSITIVITY_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "layer": INSTRUMENT_SENSITIVITY_LAYER,
            "contaminated_by_design": True,
            "source_task_id": source_task_id,
            "distillation_level": distillation_level,
            "classification": classification,
            "control_outcomes": list(control_outcomes),
            "treatment_outcomes": list(treatment_outcomes),
            "control_lineage_note": control_lineage_note,
            **(extras or {}),
        },
    )
    report_path = output_dir / "instrument_sensitivity_outcome_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = generate_manifest(
        phase=SENSITIVITY_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": None,
            }
        ],
    )
    manifest_path = output_dir / "instrument_sensitivity_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


__all__ = [
    "CLAIM_BOUNDARY",
    "INSTRUMENT_SENSITIVITY_LAYER",
    "SENSITIVITY_PHASE",
    "SENSITIVITY_VERSION",
    "classify_sensitivity",
    "fisher_exact_one_sided",
    "write_instrument_sensitivity_evidence",
]
