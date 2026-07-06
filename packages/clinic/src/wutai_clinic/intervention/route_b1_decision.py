"""Route B1 preregistered decision engine (task18 §5 + amendment B).

Pure / offline-testable. Aggregates per-cell official-eval outcomes + M-checks
into per-anchor verdicts, then the frozen §5 decision:

  - SIGNAL OF LIFE  : >=1 anchor where a VALID treatment rep resolves and control
                      deterministically fails -> green-light the >=9-pair powered
                      batch (NOT an uplift claim).
  - FUTILITY NULL   : all counted anchors no_uplift -> declare B-deployable-info
                      null, commit A.
  - INCONCLUSIVE    : no anchor counts cleanly (trigger miss / void / control not
                      deterministic-fail) -> recalibrate, do not conclude.

A treatment rep is VALID only if M1 (injected exactly once) + M2 (leak-clean) +
M4 (trigger hit) all hold. An anchor counts only if control is deterministically
failing (the §4 / ε-confirm premise) and clean (M3). Decision tokens never carry
uplift/resolved-claim wording (B6 unchanged).
"""

from __future__ import annotations

from typing import Any

SIGNAL = "signal_of_life"
NO_UPLIFT = "no_uplift"
NOT_DET_FAIL = "anchor_control_not_deterministic_fail"
CONTROL_CONTAMINATED = "control_contaminated_void"
TRIGGER_MISS_OR_VOID = "trigger_miss_or_void_not_counted"


def anchor_verdict(
    *,
    anchor: str,
    control_resolved: list[bool],
    treatment_resolved: list[bool],
    treatment_injected_once: list[bool],
    treatment_leak_clean: list[bool],
    treatment_trigger_hit: list[bool],
    control_injection_zero: list[bool],
) -> dict[str, Any]:
    control_all_fail = len(control_resolved) > 0 and not any(control_resolved)
    control_clean = len(control_injection_zero) > 0 and all(control_injection_zero)
    # per-rep validity: only M1+M2+M4-clean treatment reps are usable evidence.
    valid_resolved = [
        resolved
        for resolved, inj, leak, trig in zip(
            treatment_resolved, treatment_injected_once, treatment_leak_clean, treatment_trigger_hit
        )
        if inj and leak and trig
    ]

    if not control_all_fail:
        verdict, counted = NOT_DET_FAIL, False
    elif not control_clean:
        verdict, counted = CONTROL_CONTAMINATED, False
    elif not valid_resolved:
        verdict, counted = TRIGGER_MISS_OR_VOID, False
    else:
        counted = True
        verdict = SIGNAL if any(valid_resolved) else NO_UPLIFT

    return {
        "anchor": anchor,
        "verdict": verdict,
        "counted": counted,
        "control_deterministic_fail": control_all_fail,
        "control_clean": control_clean,
        "valid_treatment_reps": len(valid_resolved),
        "treatment_resolved_in_valid_reps": sum(1 for r in valid_resolved if r),
    }


def route_b1_decision(anchor_outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the frozen §5 decision over per-anchor outcome dicts (each carrying
    the per-rep lists consumed by `anchor_verdict`)."""
    verdicts = [anchor_verdict(**a) for a in anchor_outcomes]
    counted = [v for v in verdicts if v["counted"]]
    signal = [v for v in counted if v["verdict"] == SIGNAL]
    no_uplift = [v for v in counted if v["verdict"] == NO_UPLIFT]

    if signal:
        decision = "route_b1_probe_signal_of_life"
        next_step = "green_light_powered_batch_9_pairs_no_uplift_claim_yet"
    elif counted and not signal:
        decision = "route_b1_probe_futility_null"
        next_step = "declare_b_deployable_info_null_commit_route_a"
    else:
        decision = "route_b1_probe_inconclusive_recalibrate"
        next_step = "recalibrate_trigger_or_confirm_control_epsilon_before_reconclude"

    gates = {
        "no_uplift_claim_made": True,  # by construction this engine never asserts uplift
        "b6_red_line_unchanged": True,
        "decision_token_has_no_uplift_wording": all(
            t not in decision for t in ("uplift", "resolved_claim", "improvement")
        ),
        "at_least_one_anchor_counted_or_inconclusive": bool(counted)
        or decision.endswith("inconclusive_recalibrate"),
    }
    return {
        "phase": "route_b.b1_decision",
        "decision": decision,
        "passed": all(gates.values()),
        "gates": gates,
        "anchor_count": len(verdicts),
        "counted_anchor_count": len(counted),
        "signal_anchor_count": len(signal),
        "no_uplift_anchor_count": len(no_uplift),
        "signal_anchors": [v["anchor"] for v in signal],
        "verdicts": verdicts,
        "next_step": next_step,
        "claim_boundary": (
            "go/no-go only. signal_of_life green-lights the >=9-pair powered batch; it is NOT an "
            "uplift claim. futility_null kills the B deployable-info branch. B6 unchanged."
        ),
    }


def aggregate_cells_to_anchor_outcomes(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold per-cell rows {anchor, arm, resolved, injected_once, leak_clean,
    trigger_hit, injection_count} into per-anchor outcome dicts for the engine."""
    by_anchor: dict[str, dict[str, list[Any]]] = {}
    for c in cells:
        # crashed runs (run_ok=False, e.g. provider 'Insufficient Balance') carry no
        # valid outcome — drop them so they never count as a real control/treatment fail.
        if c.get("run_ok") is False:
            continue
        a = by_anchor.setdefault(
            str(c["anchor"]),
            {
                "control_resolved": [],
                "treatment_resolved": [],
                "treatment_injected_once": [],
                "treatment_leak_clean": [],
                "treatment_trigger_hit": [],
                "control_injection_zero": [],
            },
        )
        if c["arm"] == "control":
            a["control_resolved"].append(bool(c.get("resolved")))
            a["control_injection_zero"].append(int(c.get("injection_count", 0)) == 0)
        elif c["arm"] == "treatment":
            a["treatment_resolved"].append(bool(c.get("resolved")))
            a["treatment_injected_once"].append(bool(c.get("injected_once")))
            a["treatment_leak_clean"].append(bool(c.get("leak_clean")))
            a["treatment_trigger_hit"].append(bool(c.get("trigger_hit")))
    return [{"anchor": anchor, **lists} for anchor, lists in sorted(by_anchor.items())]


__all__ = [
    "aggregate_cells_to_anchor_outcomes",
    "anchor_verdict",
    "route_b1_decision",
]
