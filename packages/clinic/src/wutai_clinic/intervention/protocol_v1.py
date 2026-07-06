from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wutai_clinic.intervention.hooks import stable_json_hash

PROTOCOL_V1_VERSION = "protocol_v1"
PROTOCOL_V1_CLAIM_BOUNDARY = "bounded_next_step_control_no_posthoc_oracle"

PRESCRIPTION_CONSTRAINTS = {
    "targeted_failure_oracle": (
        "materialize_prefix_observed_failure",
        "block_edit_until_failure_reproduced_or_explained",
        "require_post_patch_target_recheck",
    ),
    "regression_guarded_patch_validation": (
        "require_post_patch_target_recheck",
        "require_post_patch_guard_recheck",
        "block_submit_on_guard_regression",
    ),
}

NO_UPLIFT_CLASSIFICATION_TO_PRESCRIPTION = {
    "behavior_diverged_but_target_failure_persisted": "targeted_failure_oracle",
    "target_fixed_but_regression_not_controlled": "regression_guarded_patch_validation",
}

DISALLOWED_PROTOCOL_V1_KEYS = {
    "callable",
    "code",
    "exec",
    "function",
    "lambda",
    "python",
    "script",
}


def _find_disallowed_keys(value: Any, *, path: str = "") -> list[str]:
    if isinstance(value, dict):
        matches = []
        for key, child in value.items():
            key_name = str(key)
            child_path = f"{path}.{key_name}" if path else key_name
            if key_name.lower() in DISALLOWED_PROTOCOL_V1_KEYS:
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
class ProtocolV1Trigger:
    type: str
    predicate: str
    oracle_source: str = "prefix_observation_required"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV1Trigger":
        if data.get("type") != "live_feature":
            raise ValueError("Protocol v1 only supports trigger.type=live_feature")
        predicate = str(data.get("predicate") or "")
        if not predicate:
            raise ValueError("Protocol v1 trigger.predicate is required")
        oracle_source = str(data.get("oracle_source") or "prefix_observation_required")
        if oracle_source != "prefix_observation_required":
            raise ValueError("Protocol v1 forbids posthoc or external oracle sources at runtime")
        return cls(type="live_feature", predicate=predicate, oracle_source=oracle_source)


@dataclass(frozen=True)
class ProtocolV1Action:
    type: str
    prescription_id: str
    constraint_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV1Action":
        if data.get("type") != "enforce_action_constraints":
            raise ValueError("Protocol v1 only supports action.type=enforce_action_constraints")
        prescription_id = str(data.get("prescription_id") or "")
        if prescription_id not in PRESCRIPTION_CONSTRAINTS:
            raise ValueError(f"unknown Protocol v1 prescription_id: {prescription_id}")
        expected = PRESCRIPTION_CONSTRAINTS[prescription_id]
        constraint_ids = tuple(str(item) for item in data.get("constraint_ids") or expected)
        unknown = sorted(set(constraint_ids) - set(expected))
        missing = sorted(set(expected) - set(constraint_ids))
        if unknown:
            raise ValueError(f"unknown constraints for {prescription_id}: {', '.join(unknown)}")
        if missing:
            raise ValueError(
                f"missing required constraints for {prescription_id}: {', '.join(missing)}"
            )
        return cls(
            type="enforce_action_constraints",
            prescription_id=prescription_id,
            constraint_ids=constraint_ids,
        )


@dataclass(frozen=True)
class ProtocolV1Guard:
    debounce: str = "once_per_pair"
    raw_payload_logging: bool = False
    official_eval_identifiers_runtime_visible: bool = False
    same_pair_rerun_attribution_allowed: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV1Guard":
        debounce = str(data.get("debounce") or "once_per_pair")
        if debounce != "once_per_pair":
            raise ValueError("Protocol v1 only supports guard.debounce=once_per_pair")
        if data.get("raw_payload_logging", False) is not False:
            raise ValueError("Protocol v1 requires guard.raw_payload_logging=false")
        if data.get("official_eval_identifiers_runtime_visible", False) is not False:
            raise ValueError("Protocol v1 forbids official eval identifiers in runtime prompts")
        if data.get("same_pair_rerun_attribution_allowed", False) is not False:
            raise ValueError("Protocol v1 blocks same-pair attribution after posthoc diagnosis")
        return cls()


@dataclass(frozen=True)
class ProtocolV1Claim:
    allowed: str = PROTOCOL_V1_CLAIM_BOUNDARY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV1Claim":
        allowed = str(data.get("allowed") or PROTOCOL_V1_CLAIM_BOUNDARY)
        if allowed != PROTOCOL_V1_CLAIM_BOUNDARY:
            raise ValueError(
                f"Protocol v1 only supports claim.allowed={PROTOCOL_V1_CLAIM_BOUNDARY}"
            )
        return cls(allowed=allowed)


@dataclass(frozen=True)
class ProtocolV1:
    trigger: ProtocolV1Trigger
    action: ProtocolV1Action
    guard: ProtocolV1Guard = field(default_factory=ProtocolV1Guard)
    claim: ProtocolV1Claim = field(default_factory=ProtocolV1Claim)
    version: str = PROTOCOL_V1_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolV1":
        disallowed = _find_disallowed_keys(data)
        if disallowed:
            raise ValueError(f"Protocol v1 forbids executable fields: {', '.join(disallowed)}")
        version = str(data.get("version") or PROTOCOL_V1_VERSION)
        if version != PROTOCOL_V1_VERSION:
            raise ValueError(f"unsupported Protocol v1 version: {version}")
        return cls(
            version=version,
            trigger=ProtocolV1Trigger.from_dict(data.get("trigger") or {}),
            action=ProtocolV1Action.from_dict(data.get("action") or {}),
            guard=ProtocolV1Guard.from_dict(data.get("guard") or {}),
            claim=ProtocolV1Claim.from_dict(data.get("claim") or {}),
        )

    @property
    def protocol_hash(self) -> str:
        return stable_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trigger": {
                "type": self.trigger.type,
                "predicate": self.trigger.predicate,
                "oracle_source": self.trigger.oracle_source,
            },
            "action": {
                "type": self.action.type,
                "prescription_id": self.action.prescription_id,
                "constraint_ids": list(self.action.constraint_ids),
            },
            "guard": {
                "debounce": self.guard.debounce,
                "raw_payload_logging": self.guard.raw_payload_logging,
                "official_eval_identifiers_runtime_visible": (
                    self.guard.official_eval_identifiers_runtime_visible
                ),
                "same_pair_rerun_attribution_allowed": (
                    self.guard.same_pair_rerun_attribution_allowed
                ),
            },
            "claim": {"allowed": self.claim.allowed},
        }


def protocol_v1_for_no_uplift_classification(
    *, classification: str, trigger_predicate: str
) -> ProtocolV1:
    prescription_id = NO_UPLIFT_CLASSIFICATION_TO_PRESCRIPTION.get(classification)
    if prescription_id is None:
        raise ValueError(f"unsupported no-uplift classification: {classification}")
    return ProtocolV1.from_dict(
        {
            "version": PROTOCOL_V1_VERSION,
            "trigger": {
                "type": "live_feature",
                "predicate": trigger_predicate,
                "oracle_source": "prefix_observation_required",
            },
            "action": {
                "type": "enforce_action_constraints",
                "prescription_id": prescription_id,
                "constraint_ids": list(PRESCRIPTION_CONSTRAINTS[prescription_id]),
            },
            "guard": {
                "debounce": "once_per_pair",
                "raw_payload_logging": False,
                "official_eval_identifiers_runtime_visible": False,
                "same_pair_rerun_attribution_allowed": False,
            },
            "claim": {"allowed": PROTOCOL_V1_CLAIM_BOUNDARY},
        }
    )


def build_protocol_v1_plan(no_uplift_diagnosis: dict[str, Any]) -> dict[str, Any]:
    pairs = []
    for pair in no_uplift_diagnosis.get("per_pair") or []:
        classification = str(pair.get("no_uplift_classification") or "")
        trigger = pair.get("trigger") or {}
        predicate = str(trigger.get("predicate") or "error_streak >= 1")
        protocol = protocol_v1_for_no_uplift_classification(
            classification=classification,
            trigger_predicate=predicate,
        )
        tests = pair.get("tests") or {}
        intervention_tests = tests.get("intervention") or {}
        fail_to_pass = intervention_tests.get("FAIL_TO_PASS") or {}
        pass_to_pass = intervention_tests.get("PASS_TO_PASS") or {}
        pass_to_fail = intervention_tests.get("PASS_TO_FAIL") or {}
        pairs.append(
            {
                "source_task_id": pair.get("source_task_id"),
                "pair_id": pair.get("pair_id"),
                "no_uplift_classification": classification,
                "protocol_v1": protocol.to_dict(),
                "protocol_hash": protocol.protocol_hash,
                "runtime_oracle_source": "prefix_observation_required",
                "same_pair_rerun_attribution_eligible": False,
                "official_eval_tests_analysis_only": {
                    "target_failures": list(fail_to_pass.get("failure") or []),
                    "target_successes": list(fail_to_pass.get("success") or []),
                    "guard_failures": list(pass_to_pass.get("failure") or [])
                    + list(pass_to_fail.get("failure") or []),
                },
            }
        )
    return {
        "decision": "protocol_v1_plan_ready_not_live_executed",
        "claim_boundary": (
            "Protocol v1 is a next-batch prescription plan. Official eval test identifiers are "
            "analysis-only and must not be injected into same-pair runtime prompts."
        ),
        "source_decision": no_uplift_diagnosis.get("decision"),
        "pair_count": len(pairs),
        "same_pair_positive_claim_allowed": False,
        "pairs": pairs,
    }
