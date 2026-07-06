from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wutai_clinic.intervention.hooks import POLICY_TEXT, stable_json_hash

PROTOCOL_VERSION = "protocol_v0"
CLAIM_BOUNDARY = "bounded_next_step_control"
STATE_MISMATCH_LABEL = "state_mismatch_no_attribution"
SECONDARY_AUDIT_LABEL = "secondary_audit_no_treatment_attribution"

CAPSULE_FINGERPRINT_FIELDS = (
    "task_id",
    "repo_hash",
    "agent_config_hash",
    "provider_config_hash",
    "message_prefix_hash",
    "working_tree_diff_hash",
    "observation_window_hash",
    "model_request_hash",
    "runner_config_hash",
    "deployment_hash",
    "replay_config_hash",
    "runtime_nondeterminism_policy",
)

DISALLOWED_PROTOCOL_KEYS = {
    "callable",
    "code",
    "exec",
    "function",
    "lambda",
    "python",
    "script",
}

PREDICATE_RE = re.compile(
    r"^\s*(?P<feature>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<value>true|false|-?\d+(?:\.\d+)?|\"[^\"]*\"|'[^']*')\s*$",
    re.IGNORECASE,
)

OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def _load_mapping(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        data = json.loads(path.read_text())
    elif path.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(path.read_text())
    else:
        raise ValueError("expected a .json, .yaml, or .yml file")
    if not isinstance(data, dict):
        raise ValueError("expected top-level mapping")
    return data


def _find_disallowed_keys(value: Any, *, path: str = "") -> list[str]:
    if isinstance(value, dict):
        matches = []
        for key, child in value.items():
            key_name = str(key)
            child_path = f"{path}.{key_name}" if path else key_name
            if key_name.lower() in DISALLOWED_PROTOCOL_KEYS:
                matches.append(child_path)
            matches.extend(_find_disallowed_keys(child, path=child_path))
        return matches
    if isinstance(value, list):
        matches = []
        for index, child in enumerate(value):
            matches.extend(_find_disallowed_keys(child, path=f"{path}[{index}]"))
        return matches
    return []


@dataclass(frozen=True)
class StateCapsule:
    task_id: str
    repo_hash: str
    agent_config_hash: str
    provider_config_hash: str
    message_prefix_hash: str
    working_tree_diff_hash: str
    observation_window_hash: str
    model_request_hash: str
    runner_config_hash: str
    deployment_hash: str
    replay_config_hash: str
    runtime_nondeterminism_policy: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateCapsule":
        missing = [field for field in CAPSULE_FINGERPRINT_FIELDS if not data.get(field)]
        if missing:
            raise ValueError(f"StateCapsule missing required fields: {', '.join(missing)}")
        metadata = {
            key: value
            for key, value in data.items()
            if key not in CAPSULE_FINGERPRINT_FIELDS and key != "metadata"
        }
        metadata.update(data.get("metadata") or {})
        return cls(
            **{field: str(data[field]) for field in CAPSULE_FINGERPRINT_FIELDS},
            metadata=metadata,
        )

    @classmethod
    def from_file(cls, path: Path) -> "StateCapsule":
        return cls.from_dict(_load_mapping(path))

    def fingerprint_payload(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in CAPSULE_FINGERPRINT_FIELDS}

    @property
    def fingerprint(self) -> str:
        return stable_json_hash(self.fingerprint_payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self.fingerprint_payload()
        payload["metadata"] = dict(self.metadata)
        payload["fingerprint"] = self.fingerprint
        return payload


def verify_fork_equivalence(control: StateCapsule, treatment: StateCapsule) -> dict[str, Any]:
    mismatched_fields = [
        field
        for field in CAPSULE_FINGERPRINT_FIELDS
        if getattr(control, field) != getattr(treatment, field)
    ]
    passed = not mismatched_fields and control.fingerprint == treatment.fingerprint
    return {
        "passed": passed,
        "decision": "state_capsule_equivalent" if passed else STATE_MISMATCH_LABEL,
        "control_fingerprint": control.fingerprint,
        "treatment_fingerprint": treatment.fingerprint,
        "mismatched_fields": mismatched_fields,
    }


@dataclass(frozen=True)
class ProtocolTrigger:
    type: str
    predicate: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolTrigger":
        if data.get("type") != "live_feature":
            raise ValueError("Protocol v0 only supports trigger.type=live_feature")
        predicate = str(data.get("predicate") or "")
        if not PREDICATE_RE.match(predicate):
            raise ValueError("Protocol v0 predicate must be a single comparison")
        return cls(type="live_feature", predicate=predicate)


@dataclass(frozen=True)
class ProtocolAction:
    type: str
    message_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolAction":
        if data.get("type") != "inject_system_prompt":
            raise ValueError("Protocol v0 only supports action.type=inject_system_prompt")
        message_id = str(data.get("message_id") or "")
        if message_id not in POLICY_TEXT:
            raise ValueError(f"unknown policy message_id: {message_id}")
        return cls(type="inject_system_prompt", message_id=message_id)


@dataclass(frozen=True)
class ProtocolGuard:
    debounce: str = "once_per_pair"
    raw_payload_logging: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolGuard":
        debounce = str(data.get("debounce") or "once_per_pair")
        if debounce != "once_per_pair":
            raise ValueError("Protocol v0 only supports guard.debounce=once_per_pair")
        if data.get("raw_payload_logging", False) is not False:
            raise ValueError("Protocol v0 requires guard.raw_payload_logging=false")
        return cls(debounce=debounce, raw_payload_logging=False)


@dataclass(frozen=True)
class ProtocolClaim:
    allowed: str = CLAIM_BOUNDARY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolClaim":
        allowed = str(data.get("allowed") or CLAIM_BOUNDARY)
        if allowed != CLAIM_BOUNDARY:
            raise ValueError(f"Protocol v0 only supports claim.allowed={CLAIM_BOUNDARY}")
        return cls(allowed=allowed)


@dataclass(frozen=True)
class InterventionProtocol:
    trigger: ProtocolTrigger
    action: ProtocolAction
    guard: ProtocolGuard = field(default_factory=ProtocolGuard)
    claim: ProtocolClaim = field(default_factory=ProtocolClaim)
    version: str = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterventionProtocol":
        disallowed = _find_disallowed_keys(data)
        if disallowed:
            raise ValueError(f"Protocol v0 forbids executable fields: {', '.join(disallowed)}")
        version = str(data.get("version") or PROTOCOL_VERSION)
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {version}")
        return cls(
            version=version,
            trigger=ProtocolTrigger.from_dict(data.get("trigger") or {}),
            action=ProtocolAction.from_dict(data.get("action") or {}),
            guard=ProtocolGuard.from_dict(data.get("guard") or {}),
            claim=ProtocolClaim.from_dict(data.get("claim") or {}),
        )

    @classmethod
    def from_file(cls, path: Path) -> "InterventionProtocol":
        return cls.from_dict(_load_mapping(path))

    @property
    def protocol_hash(self) -> str:
        return stable_json_hash(self.to_dict())

    @property
    def policy_message(self) -> str:
        return POLICY_TEXT[self.action.message_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trigger": {
                "type": self.trigger.type,
                "predicate": self.trigger.predicate,
            },
            "action": {
                "type": self.action.type,
                "message_id": self.action.message_id,
            },
            "guard": {
                "debounce": self.guard.debounce,
                "raw_payload_logging": self.guard.raw_payload_logging,
            },
            "claim": {"allowed": self.claim.allowed},
        }


def _parse_literal(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return value[1:-1]
    if "." in value:
        return float(value)
    return int(value)


def evaluate_trigger(protocol: InterventionProtocol, features: dict[str, Any]) -> bool:
    match = PREDICATE_RE.match(protocol.trigger.predicate)
    if match is None:
        raise ValueError("invalid predicate")
    feature_name = match.group("feature")
    if feature_name not in features:
        return False
    expected = _parse_literal(match.group("value"))
    actual = features[feature_name]
    return bool(OPERATORS[match.group("op")](actual, expected))


def simulate_protocol(
    protocol: InterventionProtocol, feature_windows: list[dict[str, Any]]
) -> dict[str, Any]:
    injected = False
    events = []
    for index, features in enumerate(feature_windows):
        triggered = evaluate_trigger(protocol, features)
        event = {
            "window_index": index,
            "triggered": triggered,
            "injected": False,
            "message_id": None,
            "raw_payload_logged": False,
        }
        if triggered and not injected:
            injected = True
            event["injected"] = True
            event["message_id"] = protocol.action.message_id
        events.append(event)
    injection_count = sum(1 for event in events if event["injected"])
    return {
        "protocol_hash": protocol.protocol_hash,
        "trigger_hit": any(event["triggered"] for event in events),
        "injection_count": injection_count,
        "events": events,
    }


def paired_replay_effect_label(
    *,
    fork_equivalence: dict[str, Any],
    trigger_hit: bool,
    injection_count: int,
    control_resolved: bool | None,
    treatment_resolved: bool | None,
) -> str:
    if fork_equivalence.get("passed") is not True:
        return STATE_MISMATCH_LABEL
    if not trigger_hit or injection_count == 0:
        return SECONDARY_AUDIT_LABEL
    if injection_count != 1:
        return "invalid_injection_count_no_attribution"
    if control_resolved is None or treatment_resolved is None:
        return "pending_or_incomplete"
    if control_resolved and treatment_resolved:
        return "both_resolved_trigger_hit_pair_no_uplift"
    if not control_resolved and treatment_resolved:
        return "intervention_only_resolved_trigger_hit_candidate"
    if control_resolved and not treatment_resolved:
        return "control_only_resolved_trigger_hit_negative_candidate"
    return "both_unresolved_trigger_hit_pair_no_uplift"


def protocol_check_report(
    *,
    protocol: InterventionProtocol,
    control_capsule: StateCapsule,
    treatment_capsule: StateCapsule,
    feature_windows: list[dict[str, Any]] | None = None,
    control_resolved: bool | None = None,
    treatment_resolved: bool | None = None,
) -> dict[str, Any]:
    fork = verify_fork_equivalence(control_capsule, treatment_capsule)
    simulation = simulate_protocol(protocol, feature_windows or [])
    effect_label = paired_replay_effect_label(
        fork_equivalence=fork,
        trigger_hit=simulation["trigger_hit"],
        injection_count=int(simulation["injection_count"]),
        control_resolved=control_resolved,
        treatment_resolved=treatment_resolved,
    )
    gates = {
        "protocol_v0_valid": True,
        "raw_payload_logging_disabled": protocol.guard.raw_payload_logging is False,
        "state_capsule_equivalent": fork["passed"],
        "single_injection_guard_satisfied": int(simulation["injection_count"]) <= 1,
        "claim_boundary_bounded": protocol.claim.allowed == CLAIM_BOUNDARY,
        "generalized_uplift_claim_not_made": True,
    }
    return {
        "phase": "4.task7.minimal_paired_intervention_replay_protocol",
        "decision": effect_label,
        "passed": all(gates.values()),
        "gates": gates,
        "protocol_hash": protocol.protocol_hash,
        "control_capsule_fingerprint": control_capsule.fingerprint,
        "treatment_capsule_fingerprint": treatment_capsule.fingerprint,
        "fork_equivalence": fork,
        "simulation": simulation,
        "claim_boundary": (
            "Single-pair replay protocol checks bounded next-step control only; it does not "
            "create a generalized causal-uplift or failure-prediction claim."
        ),
    }
