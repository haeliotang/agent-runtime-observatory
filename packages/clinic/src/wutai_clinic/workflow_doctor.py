from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PLANNING_TOKENS = (
    "approval",
    "authorization",
    "contract",
    "gate",
    "manifest",
    "plan",
    "preflight",
    "protocol",
    "readiness",
    "request",
)
EXPERIMENT_TOKENS = (
    "official_eval",
    "real_run",
    "run_events",
    "smoke",
    "evaluation",
    "predictions",
)


@dataclass
class WorkflowDoctorReport:
    root: str
    artifact_count: int
    planning_artifacts: int
    experiment_artifacts: int
    planning_ratio: float
    planning_budget: float
    decision: str
    next_action: str
    warnings: list[str]
    latest_protocol_decision: str = ""
    latest_experiment_decision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _classify(path: Path) -> str:
    name = path.name.lower()
    if any(token in name for token in EXPERIMENT_TOKENS):
        return "experiment"
    if any(token in name for token in PLANNING_TOKENS):
        return "planning"
    return "other"


def _decision_from_json(path: Path) -> str:
    if path.suffix != ".json":
        return ""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    decision = data.get("decision")
    return str(decision) if decision else ""


def diagnose_workflow(root: str | Path, planning_budget: float = 0.30) -> WorkflowDoctorReport:
    base = Path(root)
    files = [path for path in base.rglob("*") if path.is_file()]
    planning = [path for path in files if _classify(path) == "planning"]
    experiments = [path for path in files if _classify(path) == "experiment"]
    denominator = max(1, len(planning) + len(experiments))
    planning_ratio = len(planning) / denominator

    warnings: list[str] = []
    protocol_decisions = [
        decision
        for path in sorted(planning, key=lambda item: item.stat().st_mtime, reverse=True)
        for decision in [_decision_from_json(path)]
        if decision
    ]
    experiment_decisions = [
        decision
        for path in sorted(experiments, key=lambda item: item.stat().st_mtime, reverse=True)
        for decision in [_decision_from_json(path)]
        if decision
    ]
    if planning_ratio > planning_budget:
        warnings.append("planning_budget_exceeded")
    if len(planning) > len(experiments):
        warnings.append("more_planning_than_experiments")
    if protocol_decisions and "not_started" in protocol_decisions[0]:
        warnings.append("latest_protocol_not_started")

    if warnings:
        decision = "stop_adding_gates_run_bounded_experiment"
        next_action = (
            "Pick one open protocol, run the smallest bounded paired experiment, "
            "then write one outcome report before adding another gate."
        )
    else:
        decision = "workflow_balance_ok"
        next_action = "Continue extracting reusable logic or run the next planned experiment."

    return WorkflowDoctorReport(
        root=str(base),
        artifact_count=len(files),
        planning_artifacts=len(planning),
        experiment_artifacts=len(experiments),
        planning_ratio=round(planning_ratio, 6),
        planning_budget=planning_budget,
        decision=decision,
        next_action=next_action,
        warnings=warnings,
        latest_protocol_decision=protocol_decisions[0] if protocol_decisions else "",
        latest_experiment_decision=experiment_decisions[0] if experiment_decisions else "",
    )
