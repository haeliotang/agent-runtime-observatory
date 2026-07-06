from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wutai_clinic.intervention.protocol_v1 import ProtocolV1


class ProtocolV1ConstraintViolation(RuntimeError):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__(str(event.get("event") or "protocol_v1_constraint_violation"))


def _stringify_step_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("action", "command", "message", "content", "tool_input"):
            if key in value:
                return _stringify_step_value(value[key])
        return str(value)
    for attr in ("action", "command", "message", "content", "tool_input"):
        if hasattr(value, attr):
            return _stringify_step_value(getattr(value, attr))
    return str(value)


def _step_action(step: Any) -> str:
    return _stringify_step_value(step)


def _step_observation(step: Any) -> str:
    if isinstance(step, dict):
        return _stringify_step_value(step.get("observation") or step.get("output"))
    for attr in ("observation", "output"):
        if hasattr(step, attr):
            return _stringify_step_value(getattr(step, attr))
    return ""


def _is_submit_action(action: str) -> bool:
    lowered = action.lower()
    return any(token in lowered for token in ("submit", "finish", "final_answer"))


def _is_test_or_repro_action(action: str) -> bool:
    lowered = action.lower()
    return any(
        token in lowered
        for token in (
            "pytest",
            "python reproduce",
            "python repro",
            "python -m pytest",
            "tox ",
            "unittest",
        )
    )


def _is_guard_action(action: str) -> bool:
    lowered = action.lower()
    return any(token in lowered for token in ("guard", "pass_to_pass", "regression"))


def _is_source_edit_action(action: str) -> bool:
    lowered = action.lower()
    if "apply_patch" in lowered and "*** update file:" in lowered:
        return True
    if "str_replace_editor str_replace" in lowered:
        return True
    if "replace_file_content" in lowered:
        return True
    if "str_replace_editor create" in lowered and "/testbed/" in lowered:
        # Reproduction helpers are allowed before failure materialization.
        return "repro" not in lowered and "test_" not in lowered
    return False


def _observation_has_failure(observation: str) -> bool:
    lowered = observation.lower()
    return any(
        token in lowered
        for token in (
            "failed",
            "failure",
            "traceback",
            "assertionerror",
            "error:",
            "exit code 1",
            "non-zero",
        )
    )


def _observation_has_guard_regression(observation: str) -> bool:
    lowered = observation.lower()
    return any(
        token in lowered
        for token in (
            "guard regression",
            "pass_to_fail",
            "regression failed",
            "pass-to-pass failure",
        )
    )


@dataclass
class ProtocolV1RuntimeState:
    failure_materialized: bool = False
    patch_seen: bool = False
    target_rechecked_after_patch: bool = False
    guard_rechecked_after_patch: bool = False
    guard_regression_observed: bool = False
    action_index: int = 0


@dataclass
class ProtocolV1ConstraintHook:
    protocol: ProtocolV1
    source_task_id: str | None = None
    pair_id: str | None = None
    replay_prefix_action_count: int = 0
    state: ProtocolV1RuntimeState = field(default_factory=ProtocolV1RuntimeState)
    audit_events: list[dict[str, Any]] = field(default_factory=list)

    def on_init(self, *, agent: Any) -> None:
        self.agent = agent

    def on_action_started(self, *, step: Any) -> None:
        action = _step_action(step)
        event = self.before_action(action)
        if event["blocked"]:
            raise ProtocolV1ConstraintViolation(event)

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
        blocked, reason = (False, None) if in_replay_prefix else self._block_reason(action)
        event = self._event(
            event=(
                "protocol_v1_replay_action_allowed"
                if in_replay_prefix
                else "protocol_v1_action_blocked"
                if blocked
                else "protocol_v1_action_allowed"
            ),
            action_family=family,
            constraint_id=reason,
            blocked=blocked,
            replay_prefix=True if in_replay_prefix else False,
        )
        self.audit_events.append(event)
        self.state.action_index += 1
        return event

    def after_action(self, action: str, observation: str) -> dict[str, Any]:
        patch_seen_before = self.state.patch_seen
        if _is_source_edit_action(action):
            self.state.patch_seen = True
        if _is_test_or_repro_action(action) and _observation_has_failure(observation):
            self.state.failure_materialized = True
        if patch_seen_before or self.state.patch_seen:
            if _is_test_or_repro_action(action) and not _is_guard_action(action):
                self.state.target_rechecked_after_patch = True
            if _is_guard_action(action):
                self.state.guard_rechecked_after_patch = True
        if _is_guard_action(action) and _observation_has_guard_regression(observation):
            self.state.guard_regression_observed = True
        event = self._event(
            event="protocol_v1_action_observed",
            action_family=self._action_family(action),
            constraint_id=None,
            blocked=False,
            observation_failure_detected=_observation_has_failure(observation),
            guard_regression_detected=_observation_has_guard_regression(observation),
        )
        self.audit_events.append(event)
        return event

    @property
    def blocking_event_count(self) -> int:
        return sum(1 for event in self.audit_events if event.get("blocked") is True)

    def _block_reason(self, action: str) -> tuple[bool, str | None]:
        constraints = set(self.protocol.action.constraint_ids)
        if (
            "block_edit_until_failure_reproduced_or_explained" in constraints
            and _is_source_edit_action(action)
            and not self.state.failure_materialized
        ):
            return True, "block_edit_until_failure_reproduced_or_explained"
        if _is_submit_action(action) and self.state.patch_seen:
            if (
                "require_post_patch_target_recheck" in constraints
                and not self.state.target_rechecked_after_patch
            ):
                return True, "require_post_patch_target_recheck"
            if (
                "require_post_patch_guard_recheck" in constraints
                and not self.state.guard_rechecked_after_patch
            ):
                return True, "require_post_patch_guard_recheck"
            if (
                "block_submit_on_guard_regression" in constraints
                and self.state.guard_regression_observed
            ):
                return True, "block_submit_on_guard_regression"
        return False, None

    def _action_family(self, action: str) -> str:
        if _is_submit_action(action):
            return "submit"
        if _is_source_edit_action(action):
            return "source_edit"
        if _is_guard_action(action):
            return "guard_check"
        if _is_test_or_repro_action(action):
            return "target_check"
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
            "patch_seen": self.state.patch_seen,
            "target_rechecked_after_patch": self.state.target_rechecked_after_patch,
            "guard_rechecked_after_patch": self.state.guard_rechecked_after_patch,
            "guard_regression_observed": self.state.guard_regression_observed,
            "runner_started": False,
            "model_call_started": False,
            "docker_or_official_eval_started": False,
            "raw_payload_logged": False,
        }
        payload.update(extra)
        return payload
