"""Declarative manipulation checks (the M1–M4 pattern, runtime-agnostic).

A paired ablation/intervention experiment is only interpretable if the
manipulation actually happened and nothing else leaked. The cognition
ablation hand-rolled four checks (M1 arm separation, M2/M3 telemetry
completeness, M4 zero violations); this module generalizes them so any
harness can declare its checks and gate analysis on the result.

Each record is a plain dict (one telemetry row); each arm is a list of
records. A check declares which arm(s) it applies to and a predicate; the
evaluator reports per-check pass rates and an overall gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Predicate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class ManipulationCheck:
    """One M-check: ``predicate`` must hold on ``min_rate`` of the arm's rows.

    kind:
      present_in_arm — predicate holds in `arm` (e.g. M1 treatment side:
                       feature active everywhere).
      absent_in_arm  — predicate holds NOWHERE in `arm` (e.g. M1 control
                       side: feature never active; M4: zero violations).
    """

    check_id: str
    description: str
    arm: str
    predicate: Predicate
    kind: str = "present_in_arm"  # or "absent_in_arm"
    min_rate: float = 1.0


@dataclass
class CheckResult:
    check_id: str
    arm: str
    kind: str
    matched: int
    total: int
    rate: float
    passed: bool
    description: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "arm": self.arm,
            "kind": self.kind,
            "matched": self.matched,
            "total": self.total,
            "rate": self.rate,
            "passed": self.passed,
            "description": self.description,
        }


def evaluate_manipulation_checks(
    checks: list[ManipulationCheck],
    arms: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Evaluate every check; analysis may proceed only if ``all_passed``."""
    results: list[CheckResult] = []
    for check in checks:
        rows = arms.get(check.arm, [])
        matched = sum(1 for row in rows if check.predicate(row))
        total = len(rows)
        if check.kind == "absent_in_arm":
            passed = total > 0 and matched == 0
            rate = (matched / total) if total else 0.0
        else:
            rate = (matched / total) if total else 0.0
            passed = total > 0 and rate >= check.min_rate
        results.append(
            CheckResult(
                check_id=check.check_id,
                arm=check.arm,
                kind=check.kind,
                matched=matched,
                total=total,
                rate=rate,
                passed=passed,
                description=check.description,
            )
        )
    return {
        "all_passed": bool(results) and all(r.passed for r in results),
        "results": [r.to_dict() for r in results],
        "gate_semantics": (
            "Outcome analysis is interpretable only when all_passed is true; "
            "a failed manipulation check voids the batch, never just one arm."
        ),
    }


__all__ = ["CheckResult", "ManipulationCheck", "evaluate_manipulation_checks"]
