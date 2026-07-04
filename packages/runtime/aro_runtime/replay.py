"""Replay: re-execute a recorded trace and report every divergence.

A clean replay is the strongest evidence a trace offers: it means the recorded
behavior is reproducible from inputs, not just asserted. A divergence means
either the environment changed, the code changed, or the trace was tampered
with — all three are exactly the things an audit needs surfaced.
"""

from __future__ import annotations

from pathlib import Path

from aro_schema import ReplayReport, StepDivergence

from aro_runtime.executor import execute_script
from aro_runtime.policy import Policy, PolicyEngine
from aro_runtime.script import Script
from aro_runtime.tools import Workspace
from aro_runtime.trace import load_trace


def replay_trace(trace_path: Path, workspace: Workspace) -> ReplayReport:
    """Re-execute the script embedded in the trace against ``workspace``.

    ``workspace`` must be a fresh copy (it is mutated during replay).
    """
    header, recorded = load_trace(trace_path)
    script = Script.model_validate(header["script"])
    policy_engine = PolicyEngine(Policy.model_validate(header["policy"]))
    report = ReplayReport(run_id=recorded.id)

    ws_digest = workspace.digest()
    if header.get("workspace_digest") and ws_digest != header["workspace_digest"]:
        report.divergences.append(
            StepDivergence(
                step_index=-1,
                field="workspace_digest",
                recorded=header["workspace_digest"],
                replayed=ws_digest,
            )
        )

    fresh = execute_script(
        script, policy_engine=policy_engine, workspace=workspace, run_id=recorded.id
    )

    recorded_decisions = {d.step_index: d for d in recorded.policy_decisions}
    fresh_decisions = {d.step_index: d for d in fresh.policy_decisions}
    total = max(len(recorded.steps), len(fresh.steps))
    report.steps_compared = total
    for i in range(total):
        if i >= len(recorded.steps):
            report.divergences.append(
                StepDivergence(step_index=i, field="extra_step", replayed=fresh.steps[i].name)
            )
            continue
        if i >= len(fresh.steps):
            report.divergences.append(
                StepDivergence(step_index=i, field="missing_step", recorded=recorded.steps[i].name)
            )
            continue
        rec, new = recorded.steps[i], fresh.steps[i]
        for field in ("input_digest", "output_digest", "error"):
            if getattr(rec, field) != getattr(new, field):
                report.divergences.append(
                    StepDivergence(
                        step_index=i,
                        field=field,
                        recorded=getattr(rec, field),
                        replayed=getattr(new, field),
                    )
                )
        rec_d, new_d = recorded_decisions.get(i), fresh_decisions.get(i)
        rec_v = f"{rec_d.rule_id}:{rec_d.decision.value}" if rec_d else None
        new_v = f"{new_d.rule_id}:{new_d.decision.value}" if new_d else None
        if rec_v != new_v:
            report.divergences.append(
                StepDivergence(
                    step_index=i, field="policy_decision", recorded=rec_v, replayed=new_v
                )
            )
    return report
