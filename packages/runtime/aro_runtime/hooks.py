"""Hook interface the runtime exposes so telemetry backends can attach
without the runtime depending on any telemetry library."""

from __future__ import annotations

from aro_schema import AgentRun, PolicyDecision, StepRecord


class RunHooks:
    """No-op base class. Override any subset."""

    def on_run_start(self, run: AgentRun) -> None:
        pass

    def on_step_end(self, run: AgentRun, step: StepRecord, decision: PolicyDecision | None) -> None:
        pass

    def on_run_end(self, run: AgentRun) -> None:
        pass


class CompositeHooks(RunHooks):
    def __init__(self, hooks: list[RunHooks]):
        self.hooks = list(hooks)

    def on_run_start(self, run: AgentRun) -> None:
        for h in self.hooks:
            h.on_run_start(run)

    def on_step_end(self, run: AgentRun, step: StepRecord, decision: PolicyDecision | None) -> None:
        for h in self.hooks:
            h.on_step_end(run, step, decision)

    def on_run_end(self, run: AgentRun) -> None:
        for h in self.hooks:
            h.on_run_end(run)
