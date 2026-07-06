from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wutai_clinic.intervention.protocol_v1_hook import (
    _is_source_edit_action,
    _is_submit_action,
    _is_test_or_repro_action,
    _observation_has_failure,
    _step_action,
    _step_observation,
)
from wutai_clinic.intervention.protocol_v2 import ProtocolV2


class ProtocolV2ConstraintViolation(RuntimeError):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__(str(event.get("event") or "protocol_v2_constraint_violation"))


def _is_context_broadening_action(action: str) -> bool:
    lowered = action.lower()
    return any(
        token in lowered
        for token in (
            "grep",
            "rg ",
            "find ",
            "sed ",
            "cat ",
            "ls ",
            "view",
            "search",
            "read",
            "callsite",
            "symbol",
        )
    ) and not _is_source_edit_action(action)


@dataclass
class ProtocolV2RuntimeState:
    failure_materialized: bool = False
    context_broadened_after_failure: bool = False
    patch_seen: bool = False
    target_rechecked_after_patch: bool = False
    last_action_family: str | None = None
    same_action_family_streak: int = 0
    action_index: int = 0


@dataclass
class ProtocolV2ConstraintHook:
    protocol: ProtocolV2
    source_task_id: str | None = None
    pair_id: str | None = None
    replay_prefix_action_count: int = 0
    # Prescription v3 (task14): detect violations but never enforce them.
    observe_only: bool = False
    state: ProtocolV2RuntimeState = field(default_factory=ProtocolV2RuntimeState)
    audit_events: list[dict[str, Any]] = field(default_factory=list)

    def on_init(self, *, agent: Any) -> None:
        self.agent = agent

    def on_action_started(self, *, step: Any) -> None:
        action = _step_action(step)
        event = self.before_action(action)
        if event["blocked"]:
            raise ProtocolV2ConstraintViolation(event)

    def on_action_executed(self, *, step: Any) -> None:
        self.after_action(_step_action(step), _step_observation(step))

    def on_run_start(self) -> None:
        return None

    def on_step_start(self) -> None:
        return None

    def on_actions_generated(self, *, step: Any) -> None:
        return None

    def on_step_done(self, *, step: Any, info: Any) -> None:
        return None

    def on_run_done(self, *, trajectory: Any, info: Any) -> None:
        return None

    def on_setup_attempt(self) -> None:
        return None

    def on_model_query(self, *, messages: list[dict[str, Any]], agent: str) -> None:
        return None

    def on_query_message_added(
        self,
        *,
        agent: str,
        role: str,
        content: str,
        message_type: str,
        is_demo: bool = False,
        thought: str = "",
        action: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_ids: list[str] | None = None,
        thinking_blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        return None

    def on_setup_done(self) -> None:
        return None

    def on_tools_installation_started(self) -> None:
        return None

    def before_action(self, action: str) -> dict[str, Any]:
        family = self._action_family(action)
        in_replay_prefix = self.state.action_index < self.replay_prefix_action_count
        violated, reason = (False, None) if in_replay_prefix else self._block_reason(action, family)
        # Observe-only mode records the violation without enforcing it.
        blocked = violated and not self.observe_only
        if in_replay_prefix:
            event_name = "protocol_v2_replay_action_allowed"
        elif blocked:
            event_name = "protocol_v2_action_blocked"
        elif violated:
            event_name = "protocol_v2_action_would_block_observe_only"
        else:
            event_name = "protocol_v2_action_allowed"
        event = self._event(
            event=event_name,
            action_family=family,
            constraint_id=reason,
            blocked=blocked,
            would_have_blocked=violated and self.observe_only,
            replay_prefix=in_replay_prefix,
        )
        self.audit_events.append(event)
        self.state.action_index += 1
        return event

    def after_action(self, action: str, observation: str) -> dict[str, Any]:
        family = self._action_family(action)
        if family == self.state.last_action_family:
            self.state.same_action_family_streak += 1
        else:
            self.state.same_action_family_streak = 1
        self.state.last_action_family = family
        patch_seen_before = self.state.patch_seen
        if _is_test_or_repro_action(action) and _observation_has_failure(observation):
            self.state.failure_materialized = True
        if self.state.failure_materialized and _is_context_broadening_action(action):
            self.state.context_broadened_after_failure = True
        if _is_source_edit_action(action):
            self.state.patch_seen = True
        if (patch_seen_before or self.state.patch_seen) and _is_test_or_repro_action(action):
            self.state.target_rechecked_after_patch = True
        event = self._event(
            event="protocol_v2_action_observed",
            action_family=family,
            constraint_id=None,
            blocked=False,
            observation_failure_detected=_observation_has_failure(observation),
        )
        self.audit_events.append(event)
        return event

    @property
    def blocking_event_count(self) -> int:
        return sum(1 for event in self.audit_events if event.get("blocked") is True)

    def _block_reason(self, action: str, family: str) -> tuple[bool, str | None]:
        steps = set(self.protocol.action.steps)
        if (
            "interrupt_repeated_failure_loop" in steps
            and family == self.state.last_action_family
            and self.state.same_action_family_streak >= 3
            and not _is_context_broadening_action(action)
        ):
            return True, "interrupt_repeated_failure_loop"
        if (
            "require_explicit_failure_reproduction" in steps
            and _is_source_edit_action(action)
            and not self.state.failure_materialized
        ):
            return True, "require_explicit_failure_reproduction"
        if (
            "require_alternative_hypothesis_before_next_patch" in steps
            and _is_source_edit_action(action)
            and not self.state.context_broadened_after_failure
        ):
            return True, "require_alternative_hypothesis_before_next_patch"
        if (
            "require_targeted_post_patch_recheck" in steps
            and _is_submit_action(action)
            and self.state.patch_seen
            and not self.state.target_rechecked_after_patch
        ):
            return True, "require_targeted_post_patch_recheck"
        return False, None

    def _action_family(self, action: str) -> str:
        if _is_submit_action(action):
            return "submit"
        if _is_source_edit_action(action):
            return "source_edit"
        if _is_test_or_repro_action(action):
            return "target_check"
        if _is_context_broadening_action(action):
            return "context_broadening"
        return "other"

    def _event(
        self,
        *,
        event: str,
        action_family: str,
        constraint_id: str | None,
        blocked: bool,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            "event": event,
            "source_task_id": self.source_task_id,
            "pair_id": self.pair_id,
            "protocol_hash": self.protocol.protocol_hash,
            "prescription_id": self.protocol.action.prescription_id,
            "constraint_id": constraint_id,
            "action_index": self.state.action_index,
            "replay_prefix_action_count": self.replay_prefix_action_count,
            "action_family": action_family,
            "blocked": blocked,
            "failure_materialized": self.state.failure_materialized,
            "context_broadened_after_failure": self.state.context_broadened_after_failure,
            "patch_seen": self.state.patch_seen,
            "target_rechecked_after_patch": self.state.target_rechecked_after_patch,
            "same_action_family_streak": self.state.same_action_family_streak,
            "runner_started": False,
            "model_call_started": False,
            "docker_or_official_eval_started": False,
            "raw_payload_logged": False,
        }
        payload.update(extra)
        return payload


__all__ = [
    "ProtocolV2ConstraintHook",
    "ProtocolV2ConstraintViolation",
]
