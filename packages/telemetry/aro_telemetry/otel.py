"""OpenTelemetry tracing for agent runs.

Span model (see docs/telemetry-model.md):
- one ``agent_run`` span per run, carrying task/agent/status attributes;
- one ``step:<tool>`` child span per step, created at step end with the
  recorded start time and duration, carrying digests and the policy decision.

Exporter selection: OTLP if OTEL_EXPORTER_OTLP_ENDPOINT is set, console if
ARO_OTEL_CONSOLE=1, otherwise spans stay in-process (no-op cost).
"""

from __future__ import annotations

import os

from aro_runtime import RunHooks
from aro_schema import AgentRun, PolicyDecision, StepRecord
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

_configured = False


def setup_tracing(service_name: str = "aro") -> trace.Tracer:
    global _configured
    if not _configured:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        elif os.environ.get("ARO_OTEL_CONSOLE"):
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _configured = True
    return trace.get_tracer("aro")


def _ns(dt) -> int:
    return int(dt.timestamp() * 1_000_000_000)


class TracingHooks(RunHooks):
    def __init__(self, tracer: trace.Tracer | None = None):
        self.tracer = tracer or setup_tracing()
        self._run_spans: dict[str, trace.Span] = {}

    def on_run_start(self, run: AgentRun) -> None:
        span = self.tracer.start_span(
            "agent_run",
            start_time=_ns(run.started_at) if run.started_at else None,
            attributes={"aro.run_id": run.id, "aro.task_id": run.task_id, "aro.agent": run.agent},
        )
        self._run_spans[run.id] = span

    def on_step_end(self, run: AgentRun, step: StepRecord, decision: PolicyDecision | None) -> None:
        parent = self._run_spans.get(run.id)
        ctx = trace.set_span_in_context(parent) if parent else None
        start_ns = _ns(step.started_at)
        span = self.tracer.start_span(
            f"step:{step.name}",
            context=ctx,
            start_time=start_ns,
            attributes={
                "aro.run_id": run.id,
                "aro.step_index": step.index,
                "aro.tool": step.name,
                "aro.input_digest": step.input_digest,
                "aro.output_digest": step.output_digest or "",
                "aro.decision": decision.decision.value if decision else "allow",
                "aro.rule_id": decision.rule_id if decision else "",
                "aro.error": step.error or "",
            },
        )
        span.end(end_time=start_ns + int(step.duration_ms * 1_000_000))

    def on_run_end(self, run: AgentRun) -> None:
        span = self._run_spans.pop(run.id, None)
        if span is None:
            return
        span.set_attribute("aro.status", run.status.value)
        span.set_attribute("aro.steps", len(run.steps))
        span.set_attribute(
            "aro.denials",
            sum(1 for d in run.policy_decisions if d.decision.value == "deny"),
        )
        span.end(end_time=_ns(run.finished_at) if run.finished_at else None)
