"""The runtime loop: resolve args, gate through policy, execute, record.

Run-level semantics:
- a policy denial does NOT fail the run — the blocked step is recorded with
  its decision and the run continues (governance working is not an error);
- a ToolError DOES fail the run — the agent could not do what it claimed.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from aro_schema import (
    AgentRun,
    Artifact,
    Decision,
    EvidenceItem,
    PolicyDecision,
    RiskSignal,
    RunStatus,
    StepKind,
    StepRecord,
    digest_obj,
    digest_text,
    utcnow,
)

from aro_runtime.hooks import RunHooks
from aro_runtime.policy import PolicyEngine
from aro_runtime.script import Script
from aro_runtime.tools import TOOLS, ToolError, Workspace
from aro_runtime.trace import TRACE_VERSION, TraceWriter

_STEP_REF = re.compile(r"\$\{step:(\d+)\.output\}")

PREVIEW_CHARS = 200


def _resolve_args(args: dict, outputs: list[str | None]) -> dict:
    """Substitute ``${step:N.output}`` references with prior step outputs."""

    def resolve(value):
        if isinstance(value, str):

            def sub(m: re.Match) -> str:
                idx = int(m.group(1))
                if idx >= len(outputs) or outputs[idx] is None:
                    raise ToolError(f"step {idx} has no output to reference")
                return outputs[idx]

            return _STEP_REF.sub(sub, value)
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    return {k: resolve(v) for k, v in args.items()}


def execute_script(
    script: Script,
    *,
    policy_engine: PolicyEngine,
    workspace: Workspace,
    run_id: str | None = None,
    hooks: RunHooks | None = None,
    trace_path: Path | None = None,
) -> AgentRun:
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    hooks = hooks or RunHooks()
    run = AgentRun(
        id=run_id,
        task_id=script.task.id,
        agent=script.agent,
        model=script.model,
        status=RunStatus.RUNNING,
        started_at=utcnow(),
    )
    hooks.on_run_start(run)

    writer = TraceWriter(trace_path) if trace_path else None
    if writer:
        writer.event(
            "run_start",
            header={
                "version": TRACE_VERSION,
                "run_id": run_id,
                "script": script.model_dump(mode="json"),
                "policy": policy_engine.policy.model_dump(mode="json"),
                "workspace_digest": workspace.digest(),
            },
        )

    outputs: list[str | None] = []
    failed = False
    for i, scripted in enumerate(script.steps):
        t0 = time.perf_counter()
        step = StepRecord(index=i, name=scripted.tool, input_digest="", started_at=utcnow())
        pd: PolicyDecision | None = None
        try:
            args = _resolve_args(scripted.args, outputs)
        except ToolError as exc:
            step.args = scripted.args
            step.input_digest = digest_obj({"tool": scripted.tool, "args": scripted.args})
            step.error = str(exc)
            outputs.append(None)
            failed = True
        else:
            step.args = args
            step.input_digest = digest_obj({"tool": scripted.tool, "args": args})
            decision, rule, reason = policy_engine.evaluate(scripted.tool, args)
            if rule is not None:
                pd = PolicyDecision(
                    id=f"{run_id}-pd-{i}",
                    run_id=run_id,
                    step_index=i,
                    policy_id=policy_engine.policy.id,
                    rule_id=rule.id,
                    decision=decision,
                    reason=reason,
                )
                run.policy_decisions.append(pd)
                step.decision_id = pd.id
                if writer:
                    writer.event("policy_decision", decision=pd.model_dump(mode="json"))
                if decision in (Decision.DENY, Decision.NEEDS_REVIEW):
                    signal = RiskSignal(
                        id=f"{run_id}-rs-{i}",
                        run_id=run_id,
                        step_index=i,
                        severity=rule.severity,
                        category=rule.category,
                        message=f"rule {rule.id} matched {scripted.tool}: {reason}",
                    )
                    run.risk_signals.append(signal)
                    if writer:
                        writer.event("risk_signal", signal=signal.model_dump(mode="json"))
            if decision == Decision.DENY:
                step.error = f"blocked by policy rule {rule.id}"
                outputs.append(None)
            else:
                try:
                    output = TOOLS[scripted.tool](workspace, args)
                except ToolError as exc:
                    step.error = str(exc)
                    outputs.append(None)
                    failed = True
                else:
                    step.output_digest = digest_text(output)
                    step.output_preview = output[:PREVIEW_CHARS]
                    outputs.append(output)
                    item = EvidenceItem(
                        id=f"{run_id}-ev-{i}",
                        run_id=run_id,
                        step_index=i,
                        kind="tool_output",
                        digest=step.output_digest,
                        description=f"output of {scripted.tool}",
                    )
                    run.evidence.append(item)
                    if writer:
                        writer.event("evidence", item=item.model_dump(mode="json"))
                    if scripted.tool == "write_file":
                        step.kind = StepKind.ARTIFACT_WRITE
                        content = args["content"]
                        artifact = Artifact(
                            id=f"{run_id}-art-{i}",
                            run_id=run_id,
                            path=args["path"],
                            digest=digest_text(content),
                            media_type="text/markdown"
                            if args["path"].endswith(".md")
                            else "text/plain",
                            size_bytes=len(content.encode()),
                        )
                        run.artifacts.append(artifact)
                        if writer:
                            writer.event("artifact", artifact=artifact.model_dump(mode="json"))
        step.duration_ms = (time.perf_counter() - t0) * 1000
        run.steps.append(step)
        if writer:
            writer.event("step", step=step.model_dump(mode="json"))
        hooks.on_step_end(run, step, pd)

    run.status = RunStatus.FAILED if failed else RunStatus.COMPLETED
    run.finished_at = utcnow()
    if writer:
        writer.event(
            "run_end",
            status=run.status.value,
            started_at=run.started_at.isoformat(),
            finished_at=run.finished_at.isoformat(),
        )
        writer.close()
    hooks.on_run_end(run)
    return run
