from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    evaluate_trigger,
    verify_fork_equivalence,
)

HybridPhase = Literal["replay", "generation"]
ArmType = Literal["control", "treatment"]


class QueryDelegate(Protocol):
    def query(self, history: list[dict[str, Any]]) -> dict[str, Any]: ...


class CapsuleBuilder(Protocol):
    def __call__(self, context: "CapsuleBuildContext") -> StateCapsule: ...


class FeatureExtractor(Protocol):
    def __call__(self, context: "CapsuleBuildContext") -> dict[str, Any]: ...


@dataclass
class EmptyModelStats:
    def model_dump(self) -> dict[str, Any]:
        return {}


_VOLATILE_MESSAGE_KEYS = {
    "cache_control",
    "id",
    "provider_specific_fields",
    "tool_call_id",
    "tool_call_ids",
}
_DURATION_RE = re.compile(
    r"(?P<prefix>\b(?:in|within|took|elapsed|duration:)\s+)"
    r"(?P<value>\d+(?:\.\d+)?)"
    r"(?P<suffix>\s*(?:s|sec|secs|second|seconds)\b)",
    re.IGNORECASE,
)
_PYTEST_DURATION_RE = re.compile(r"(?P<prefix>\bpassed in )\d+(?:\.\d+)?s\b")


def _normalize_volatile_text(value: str) -> str:
    value = _DURATION_RE.sub(r"\g<prefix><DURATION>\g<suffix>", value)
    return _PYTEST_DURATION_RE.sub(r"\g<prefix><DURATION>s", value)


def _normalize_message_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_volatile_text(value)
    if isinstance(value, list):
        return [_normalize_message_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_message_value(child) for key, child in value.items()}
    return value


def normalize_live_message_prefix(messages: list[dict[str, Any]]) -> None:
    """Normalize non-semantic runtime noise before capsule hashing and provider calls."""

    for message in messages:
        for key, value in list(message.items()):
            message[key] = _normalize_message_value(value)


def _canonical_json_argument(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _canonical_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") or {}
    payload: dict[str, Any] = {
        "type": tool_call.get("type"),
        "function": {
            "name": function.get("name"),
            "arguments": _canonical_json_argument(function.get("arguments")),
        },
    }
    return payload


def _canonical_content(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_volatile_text(value)
    if isinstance(value, dict):
        return {
            str(key): _canonical_content(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _VOLATILE_MESSAGE_KEYS
        }
    if isinstance(value, list):
        return [_canonical_content(item) for item in value]
    return value


def _canonical_message(message: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("role", "agent", "message_type", "content", "thought", "action", "name"):
        if key in message:
            payload[key] = _canonical_content(message[key])
    if isinstance(message.get("tool_calls"), list):
        payload["tool_calls"] = [
            _canonical_tool_call(tool_call)
            for tool_call in message["tool_calls"]
            if isinstance(tool_call, dict)
        ]
    if isinstance(message.get("tool_call"), dict):
        payload["tool_call"] = _canonical_tool_call(message["tool_call"])
    return payload


def message_prefix_hash(messages: list[dict[str, Any]]) -> str:
    """Hash the semantic conversation prefix without transport-generated ids."""

    return stable_json_hash([_canonical_message(message) for message in messages])


def normalize_model_output(action: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(action, str):
        return {"message": action}
    output = copy.deepcopy(action)
    if "message" not in output:
        raise ValueError("replay action dict must include a message field")
    return output


@dataclass
class HybridQueryEvent:
    query_index: int
    phase: HybridPhase
    delegated: bool
    output_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_index": self.query_index,
            "phase": self.phase,
            "delegated": self.delegated,
            "output_sha256": self.output_sha256,
        }


class HybridReplayGenerationModel:
    """Replay a frozen prefix, then delegate future queries to the live model."""

    def __init__(
        self,
        *,
        replay_actions: list[dict[str, Any] | str],
        delegate: QueryDelegate,
        replay_until: int | None = None,
    ):
        replay_until = len(replay_actions) if replay_until is None else replay_until
        if replay_until < 0:
            raise ValueError("replay_until must be non-negative")
        if replay_until > len(replay_actions):
            raise ValueError("replay_until cannot exceed replay_actions length")
        self.replay_actions = [normalize_model_output(action) for action in replay_actions]
        self.delegate = delegate
        self.replay_until = replay_until
        self.query_count = 0
        self.events: list[HybridQueryEvent] = []

    @property
    def stats(self) -> Any:
        return getattr(self.delegate, "stats", EmptyModelStats())

    @property
    def next_phase(self) -> HybridPhase:
        return "replay" if self.query_count < self.replay_until else "generation"

    @property
    def replay_complete(self) -> bool:
        return self.query_count >= self.replay_until

    def query(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        query_index = self.query_count
        phase = self.next_phase
        if phase == "replay":
            output = copy.deepcopy(self.replay_actions[query_index])
            delegated = False
        else:
            output = self.delegate.query(history)
            delegated = True
        self.query_count += 1
        self.events.append(
            HybridQueryEvent(
                query_index=query_index,
                phase=phase,
                delegated=delegated,
                output_sha256=stable_json_hash(output),
            )
        )
        return output

    def event_rows(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]


@dataclass(frozen=True)
class CapsuleBuildContext:
    arm_type: ArmType
    agent_name: str
    query_index: int
    messages: list[dict[str, Any]]
    agent: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapsuleInjectionEvent:
    arm_type: ArmType
    agent_name: str
    query_index: int
    phase: HybridPhase
    capsule_fingerprint: str | None
    fork_decision: str
    fork_passed: bool
    trigger_hit: bool
    injected: bool
    message_count_before: int
    message_count_after: int
    pre_intervention_message_prefix_hash: str
    post_intervention_request_hash: str
    raw_payload_logged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_type": self.arm_type,
            "agent_name": self.agent_name,
            "query_index": self.query_index,
            "phase": self.phase,
            "capsule_fingerprint": self.capsule_fingerprint,
            "fork_decision": self.fork_decision,
            "fork_passed": self.fork_passed,
            "trigger_hit": self.trigger_hit,
            "injected": self.injected,
            "message_count_before": self.message_count_before,
            "message_count_after": self.message_count_after,
            "message_delta": self.message_count_after - self.message_count_before,
            "pre_intervention_message_prefix_hash": self.pre_intervention_message_prefix_hash,
            "post_intervention_request_hash": self.post_intervention_request_hash,
            "raw_payload_logged": self.raw_payload_logged,
        }


class CapsuleMaterializationHook:
    """Materialize a capsule at the first generation query and optionally inject Protocol v0."""

    def __init__(
        self,
        *,
        arm_type: ArmType,
        protocol: InterventionProtocol,
        capsule_builder: CapsuleBuilder,
        feature_extractor: FeatureExtractor | None = None,
        reference_capsule: StateCapsule | None = None,
    ):
        self.arm_type = arm_type
        self.protocol = protocol
        self.capsule_builder = capsule_builder
        self.feature_extractor = feature_extractor or (lambda _context: {})
        self.reference_capsule = reference_capsule
        self.agent: Any | None = None
        self.capsule: StateCapsule | None = None
        self.injected = False
        self.safe_audit_events: list[dict[str, Any]] = []

    def on_init(self, *, agent: Any) -> None:
        self.agent = agent

    def on_run_start(self) -> None:
        pass

    def on_step_start(self) -> None:
        pass

    def on_actions_generated(self, *, step: Any) -> None:
        pass

    def on_action_started(self, *, step: Any) -> None:
        pass

    def on_action_executed(self, *, step: Any) -> None:
        pass

    def on_step_done(self, *, step: Any, info: Any) -> None:
        pass

    def on_run_done(self, *, trajectory: Any, info: Any) -> None:
        pass

    def on_setup_attempt(self) -> None:
        pass

    def on_query_message_added(self, **_kwargs: Any) -> None:
        pass

    def on_setup_done(self) -> None:
        pass

    def on_tools_installation_started(self) -> None:
        pass

    def on_model_query(self, *, messages: list[dict[str, Any]], agent: str) -> None:
        phase = self._next_phase()
        if phase != "generation" or self.capsule is not None:
            return

        normalize_live_message_prefix(messages)
        before_count = len(messages)
        pre_hash = message_prefix_hash(messages)
        query_index = self._query_index()
        context = CapsuleBuildContext(
            arm_type=self.arm_type,
            agent_name=agent,
            query_index=query_index,
            messages=copy.deepcopy(messages),
            agent=self.agent,
        )
        capsule = self.capsule_builder(context)
        self.capsule = capsule
        fork = self._fork_equivalence(capsule)
        features = self.feature_extractor(context)
        trigger_hit = evaluate_trigger(self.protocol, features)
        should_inject = (
            self.arm_type == "treatment"
            and fork["passed"] is True
            and trigger_hit
            and not self.injected
        )
        if should_inject:
            messages.append({"role": "system", "content": self.protocol.policy_message})
            self.injected = True
        post_hash = message_prefix_hash(messages)
        event = CapsuleInjectionEvent(
            arm_type=self.arm_type,
            agent_name=agent,
            query_index=query_index,
            phase=phase,
            capsule_fingerprint=capsule.fingerprint,
            fork_decision=str(fork["decision"]),
            fork_passed=bool(fork["passed"]),
            trigger_hit=trigger_hit,
            injected=should_inject,
            message_count_before=before_count,
            message_count_after=len(messages),
            pre_intervention_message_prefix_hash=pre_hash,
            post_intervention_request_hash=post_hash,
        )
        self.safe_audit_events.append(event.to_dict())

    @property
    def injection_count(self) -> int:
        return int(self.injected)

    def _next_phase(self) -> HybridPhase:
        model = getattr(self.agent, "model", None)
        return getattr(model, "next_phase", "generation")

    def _query_index(self) -> int:
        model = getattr(self.agent, "model", None)
        return int(getattr(model, "query_count", 0))

    def _fork_equivalence(self, capsule: StateCapsule) -> dict[str, Any]:
        if self.reference_capsule is None:
            return {
                "passed": True,
                "decision": "state_capsule_materialized_no_reference",
                "control_fingerprint": capsule.fingerprint,
                "treatment_fingerprint": capsule.fingerprint,
                "mismatched_fields": [],
            }
        return verify_fork_equivalence(self.reference_capsule, capsule)
