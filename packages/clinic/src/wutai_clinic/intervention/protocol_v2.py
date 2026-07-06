from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wutai_clinic.intervention.hooks import stable_json_hash

PROTOCOL_V2_VERSION = "protocol_v2_prescription"
PROTOCOL_V2_CLAIM_BOUNDARY = "prospective_batch_prescription_no_outcome_oracle"

DISALLOWED_PROTOCOL_V2_KEYS = {
    "callable",
    "code",
    "exec",
    "function",
    "lambda",
    "python",
    "script",
}
DISALLOWED_RUNTIME_ORACLE_TERMS = {
    "official_eval",
    "resolved",
    "unresolved",
    "fail_to_pass",
    "pass_to_pass",
    "pass_to_fail",
    "test_patch",
}
PRESCRIPTION_STEPS = {
    "break_recurrence_and_reproduce": (
        "interrupt_repeated_failure_loop",
        "require_explicit_failure_reproduction",
        "require_alternative_hypothesis_before_next_patch",
        "require_targeted_post_patch_recheck",
    ),
    "broaden_context_then_validate": (
        "interrupt_local_file_fixation",
        "require_adjacent_symbol_or_callsite_scan",
        "require_hypothesis_update_from_new_context",
        "require_targeted_post_patch_recheck",
    ),
}


def _find_disallowed_keys(value: Any, *, path: str = "") -> list[str]:
    if isinstance(value, dict):
        matches = []
        for key, child in value.items():
            key_name = str(key)
            child_path = f"{path}.{key_name}" if path else key_name
            if key_name.lower() in DISALLOWED_PROTOCOL_V2_KEYS:
                matches.append(child_path)
            matches.extend(_find_disallowed_keys(child, path=child_path))
        return matches
    if isinstance(value, (list, tuple)):
        matches = []
        for index, child in enumerate(value):
            matches.extend(_find_disallowed_keys(child, path=f"{path}[{index}]"))
        return matches
    return []


def _find_runtime_oracle_terms(value: Any, *, path: str = "") -> list[str]:
    matches = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            matches.extend(_find_runtime_oracle_terms(child, path=child_path))
        return matches
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            matches.extend(_find_runtime_oracle_terms(child, path=f"{path}[{index}]"))
        return matches
    if isinstance(value, str):
        lowered = value.lower()
        for term in sorted(DISALLOWED_RUNTIME_ORACLE_TERMS):
            if term in lowered:
                matches.append(f"{path}:{term}")
    return matches


@dataclass(frozen=True)
class ProtocolV2Trigger:
    type: str
    predicates: tuple[str, ...]
    evidence_sources: tuple[str, ...] = ("live_feature", "prefix_observation")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV2Trigger":
        if data.get("type") != "live_feature_conjunction":
            raise ValueError("Protocol v2 only supports trigger.type=live_feature_conjunction")
        predicates = tuple(str(item) for item in data.get("predicates") or [])
        if not predicates:
            raise ValueError("Protocol v2 trigger.predicates is required")
        sources = tuple(str(item) for item in data.get("evidence_sources") or [])
        allowed_sources = {"live_feature", "prefix_observation"}
        if not sources or any(source not in allowed_sources for source in sources):
            raise ValueError(
                "Protocol v2 trigger evidence must come from live features/prefix observations"
            )
        oracle_terms = _find_runtime_oracle_terms({"predicates": predicates, "sources": sources})
        if oracle_terms:
            raise ValueError(
                f"Protocol v2 forbids official outcome/test oracles at runtime: {', '.join(oracle_terms)}"
            )
        return cls(type="live_feature_conjunction", predicates=predicates, evidence_sources=sources)


@dataclass(frozen=True)
class ProtocolV2Action:
    type: str
    prescription_id: str
    steps: tuple[str, ...]
    prompt_style: str = "directive_but_non_oracular"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV2Action":
        if data.get("type") != "execute_prescription_steps":
            raise ValueError("Protocol v2 only supports action.type=execute_prescription_steps")
        prescription_id = str(data.get("prescription_id") or "")
        if prescription_id not in PRESCRIPTION_STEPS:
            raise ValueError(f"unknown Protocol v2 prescription_id: {prescription_id}")
        expected = PRESCRIPTION_STEPS[prescription_id]
        steps = tuple(str(item) for item in data.get("steps") or expected)
        unknown = sorted(set(steps) - set(expected))
        missing = sorted(set(expected) - set(steps))
        if unknown:
            raise ValueError(
                f"unknown prescription steps for {prescription_id}: {', '.join(unknown)}"
            )
        if missing:
            raise ValueError(
                f"missing required prescription steps for {prescription_id}: {', '.join(missing)}"
            )
        prompt_style = str(data.get("prompt_style") or "directive_but_non_oracular")
        if prompt_style != "directive_but_non_oracular":
            raise ValueError("Protocol v2 prompt_style must be directive_but_non_oracular")
        return cls(
            type="execute_prescription_steps",
            prescription_id=prescription_id,
            steps=steps,
            prompt_style=prompt_style,
        )


@dataclass(frozen=True)
class ProtocolV2Guard:
    max_injections_per_pair: int = 1
    raw_payload_logging: bool = False
    official_eval_identifiers_runtime_visible: bool = False
    test_identifiers_runtime_visible: bool = False
    same_pair_positive_claim_allowed: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV2Guard":
        max_injections = int(data.get("max_injections_per_pair") or 1)
        if max_injections != 1:
            raise ValueError("Protocol v2 only supports max_injections_per_pair=1")
        if data.get("raw_payload_logging", False) is not False:
            raise ValueError("Protocol v2 requires guard.raw_payload_logging=false")
        if data.get("official_eval_identifiers_runtime_visible", False) is not False:
            raise ValueError("Protocol v2 forbids official eval identifiers in runtime prompts")
        if data.get("test_identifiers_runtime_visible", False) is not False:
            raise ValueError("Protocol v2 forbids official test identifiers in runtime prompts")
        if data.get("same_pair_positive_claim_allowed", False) is not False:
            raise ValueError("Protocol v2 blocks same-pair positive attribution claims")
        return cls()


@dataclass(frozen=True)
class ProtocolV2Claim:
    allowed: str = PROTOCOL_V2_CLAIM_BOUNDARY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV2Claim":
        allowed = str(data.get("allowed") or PROTOCOL_V2_CLAIM_BOUNDARY)
        if allowed != PROTOCOL_V2_CLAIM_BOUNDARY:
            raise ValueError(
                f"Protocol v2 only supports claim.allowed={PROTOCOL_V2_CLAIM_BOUNDARY}"
            )
        return cls(allowed=allowed)


@dataclass(frozen=True)
class ProtocolV2:
    trigger: ProtocolV2Trigger
    action: ProtocolV2Action
    guard: ProtocolV2Guard = field(default_factory=ProtocolV2Guard)
    claim: ProtocolV2Claim = field(default_factory=ProtocolV2Claim)
    version: str = PROTOCOL_V2_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV2":
        disallowed = _find_disallowed_keys(data)
        if disallowed:
            raise ValueError(f"Protocol v2 forbids executable fields: {', '.join(disallowed)}")
        version = str(data.get("version") or PROTOCOL_V2_VERSION)
        if version != PROTOCOL_V2_VERSION:
            raise ValueError(f"unsupported Protocol v2 version: {version}")
        return cls(
            version=version,
            trigger=ProtocolV2Trigger.from_dict(data.get("trigger") or {}),
            action=ProtocolV2Action.from_dict(data.get("action") or {}),
            guard=ProtocolV2Guard.from_dict(data.get("guard") or {}),
            claim=ProtocolV2Claim.from_dict(data.get("claim") or {}),
        )

    @property
    def protocol_hash(self) -> str:
        return stable_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trigger": {
                "type": self.trigger.type,
                "predicates": list(self.trigger.predicates),
                "evidence_sources": list(self.trigger.evidence_sources),
            },
            "action": {
                "type": self.action.type,
                "prescription_id": self.action.prescription_id,
                "steps": list(self.action.steps),
                "prompt_style": self.action.prompt_style,
            },
            "guard": {
                "max_injections_per_pair": self.guard.max_injections_per_pair,
                "raw_payload_logging": self.guard.raw_payload_logging,
                "official_eval_identifiers_runtime_visible": (
                    self.guard.official_eval_identifiers_runtime_visible
                ),
                "test_identifiers_runtime_visible": self.guard.test_identifiers_runtime_visible,
                "same_pair_positive_claim_allowed": self.guard.same_pair_positive_claim_allowed,
            },
            "claim": {"allowed": self.claim.allowed},
        }


def protocol_v2_prescription_template(
    *,
    prescription_id: str = "break_recurrence_and_reproduce",
    predicates: tuple[str, ...] = (
        "error_streak >= 2",
        "same_failure_family_repeated is true",
        "target_failure_not_materialized_after_replay is true",
    ),
) -> ProtocolV2:
    return ProtocolV2.from_dict(
        {
            "version": PROTOCOL_V2_VERSION,
            "trigger": {
                "type": "live_feature_conjunction",
                "predicates": list(predicates),
                "evidence_sources": ["live_feature", "prefix_observation"],
            },
            "action": {
                "type": "execute_prescription_steps",
                "prescription_id": prescription_id,
                "steps": list(PRESCRIPTION_STEPS[prescription_id]),
                "prompt_style": "directive_but_non_oracular",
            },
            "guard": {
                "max_injections_per_pair": 1,
                "raw_payload_logging": False,
                "official_eval_identifiers_runtime_visible": False,
                "test_identifiers_runtime_visible": False,
                "same_pair_positive_claim_allowed": False,
            },
            "claim": {"allowed": PROTOCOL_V2_CLAIM_BOUNDARY},
        }
    )


__all__ = [
    "PROTOCOL_V2_CLAIM_BOUNDARY",
    "PROTOCOL_V2_VERSION",
    "ProtocolV2",
    "protocol_v2_prescription_template",
]
