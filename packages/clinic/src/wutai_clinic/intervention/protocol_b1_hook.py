"""Route B1 injection hook — captures the issue-derived reproduction in the LIVE
container at the first model query, then injects it ONCE.

Timing matters: the SWE-bench container/runtime is only started inside
RunSingle.run(), so the reproduction must be captured DURING the run, not before
(pre-run capture raised DeploymentNotStartedError). The first on_model_query is a
guaranteed-live moment (the agent is actively querying to act), so capture +
post-capture M2b + injection all happen there, exactly once.

M2b is enforced here on the LIVE-captured payload: if the issue-derived
reproduction (or its traceback) overlaps FAIL_TO_PASS / test_patch / gold, the
arm is voided and nothing is injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wutai_clinic.intervention.b1_issue_repro import (
    ISSUE_TEXT_ONLY,
    b1_payload_leak_scan,
    capture_issue_repro,
)
from wutai_clinic.intervention.protocol_b1 import ProtocolB1

# injector(agent, payload) -> None. Performs the environment-specific context
# injection. Default is a pure recorder so the hook is offline-testable.
B1Injector = Callable[[Any, dict[str, Any]], None]
# executor(script) -> combined output. Runs an issue-derived repro in the live container.
ReproExecutor = Callable[[str], str]


class ProtocolB1InjectionVoid(RuntimeError):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__(str(event.get("event") or "protocol_b1_injection_void"))


@dataclass
class ProtocolB1InjectionHook:
    protocol: ProtocolB1
    issue_reproduction_steps: str
    source_task_id: str | None = None
    pair_id: str | None = None
    # live container exec (built from the started env); None => no capture (steps-only payload)
    capture_executor: ReproExecutor | None = None
    # M2b leak refs — used ONLY to scan the captured payload, never injected
    fail_to_pass: list[str] = field(default_factory=list)
    test_patch: str | None = None
    gold_patch: str | None = None
    injector: B1Injector | None = None
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    injected_count: int = 0
    captured_traceback: str | None = None
    capture_leak_findings: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    agent: Any = None

    def on_init(self, *, agent: Any) -> None:
        self.agent = agent

    def on_model_query(self, *, messages: list[dict[str, Any]], agent: str) -> None:
        self._maybe_capture_and_inject(reason="first_model_query")

    def _maybe_capture_and_inject(self, *, reason: str) -> None:
        if self.injected_count >= int(self.protocol.guard.max_injections_per_pair):
            return
        if self.payload is not None:  # already attempted this run
            return
        # 1) capture the issue-derived reproduction in the LIVE container. A capture
        #    error must NOT kill the run — degrade to steps-only (traceback=None).
        try:
            cap = capture_issue_repro(
                issue_reproduction_steps=self.issue_reproduction_steps,
                executor=self.capture_executor or (lambda _script: ""),
            )
            self.captured_traceback = cap.traceback
        except Exception as exc:  # pragma: no cover - live container failure path
            self.captured_traceback = None
            self.audit_events.append(self._event(event="protocol_b1_capture_error", injected=False, error=str(exc)[:200]))
        payload = {
            "instance_id": self.source_task_id,
            "info_kind": self.protocol.action.info_kind,
            "payload_provenance": ISSUE_TEXT_ONLY,
            "issue_reproduction_steps": self.issue_reproduction_steps,
            "issue_derived_repro_traceback": self.captured_traceback,
        }
        # 2) post-capture M2b: the captured traceback could surface official test identity.
        #    On leak, record + skip injection (do NOT raise — SWE-agent may swallow it;
        #    the adapter reads capture_leak_findings after the run and voids the arm).
        self.capture_leak_findings = b1_payload_leak_scan(
            payload, fail_to_pass=self.fail_to_pass, test_patch=self.test_patch, gold_patch=self.gold_patch
        )
        self.payload = payload
        if self.capture_leak_findings:
            self.audit_events.append(
                self._event(event="protocol_b1_capture_leak_void", injected=False, findings=self.capture_leak_findings)
            )
            return
        # 3) inject once
        if self.injector is not None:
            self.injector(self.agent, payload)
        self.injected_count += 1
        self.audit_events.append(
            self._event(event="protocol_b1_injection", injected=True, trigger_reason=reason)
        )

    @property
    def injection_count(self) -> int:
        return self.injected_count

    def _event(self, *, event: str, injected: bool, **extra: Any) -> dict[str, Any]:
        payload = {
            "event": event,
            "source_task_id": self.source_task_id,
            "pair_id": self.pair_id,
            "protocol_hash": self.protocol.protocol_hash,
            "info_kind": self.protocol.action.info_kind,
            "payload_provenance": ISSUE_TEXT_ONLY,
            "injected": injected,
            "injected_count": self.injected_count,
            "max_injections": self.protocol.guard.max_injections_per_pair,
            "traceback_captured": self.captured_traceback is not None,
            "runner_started": False,
            "model_call_started": False,
            "docker_or_official_eval_started": False,
            "raw_payload_logged": False,
        }
        payload.update(extra)
        return payload

    # --- inert SWE-agent hook surface ----------------------------------------
    def on_run_start(self) -> None: return None
    def on_step_start(self) -> None: return None
    def on_action_started(self, *, step: Any) -> None: return None
    def on_actions_generated(self, *, step: Any) -> None: return None
    def on_action_executed(self, *, step: Any) -> None: return None
    def on_step_done(self, *, step: Any, info: Any) -> None: return None
    def on_run_done(self, *, trajectory: Any, info: Any) -> None: return None
    def on_setup_attempt(self) -> None: return None
    def on_setup_done(self) -> None: return None
    def on_tools_installation_started(self) -> None: return None
    def on_query_message_added(self, **kwargs: Any) -> None: return None


__all__ = ["B1Injector", "ProtocolB1InjectionHook", "ProtocolB1InjectionVoid", "ReproExecutor"]
