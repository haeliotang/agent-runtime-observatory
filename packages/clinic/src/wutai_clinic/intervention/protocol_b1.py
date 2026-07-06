"""Protocol B1 — reproduction-first deployable information injection (Route B probe).

This is NOT a v0/v1/v2 constraint hook. It is the Route B "missing middle"
between task16's oracle injection (cheating, WON) and the v1/v2 constraint hooks
(deployable, NULL): inject a *deployable, non-oracle* information signal that the
agent could obtain itself (the failing test's reproduction traceback + asserted
expectation), with the gold patch / oracle / official-eval outcome structurally
forbidden.

The anti-oracle-leakage line (guard.oracle_capsule_allowed == False, the
forbidden payload categories, and the allowed-field whitelist) is what separates
B1 from task16's oracle. It carries CLAIMS C4: go/no-go only, never an uplift
claim (B6 unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wutai_clinic.intervention.hooks import stable_json_hash

PROTOCOL_B1_VERSION = "protocol_b1_issue_text_repro"
PROTOCOL_B1_CLAIM_BOUNDARY = "route_b_go_no_go_no_uplift_claim"

# Amendment A (2026-06-14): reproduction is ISSUE-TEXT-ONLY. The previous
# "reproduction_first" form (running the failing test = FAIL_TO_PASS and
# injecting its traceback) leaked the benchmark oracle and collapsed B1 into
# task16. The only deployable info kind is now an issue-derived reproduction; the
# leaking fields are removed so the invalid form is INEXPRESSIBLE in code.
ISSUE_TEXT_ONLY = "issue_text_only"
INFO_KIND_ALLOWED_FIELDS: dict[str, tuple[str, ...]] = {
    "issue_text_reproduction": (
        "issue_reproduction_steps",  # quoted from problem_statement
        "issue_derived_repro_traceback",  # agent runs its OWN issue-derived repro
    ),
}

# Categories the payload must structurally forbid — these are the oracle/answer
# leakage classes that would collapse B1 back into task16. A valid B1 guard must
# declare a forbidden set that is a SUPERSET of this. `official_test_identity`
# (Amendment A / M2b) blocks FAIL_TO_PASS / test_patch / official test names.
REQUIRED_FORBIDDEN_CATEGORIES = frozenset(
    {
        "gold_patch",
        "fix_diff",
        "oracle_capsule",
        "official_eval_outcome",
        "hidden_test_oracle",
        "official_test_identity",
    }
)

DISALLOWED_EXECUTABLE_KEYS = {
    "callable",
    "code",
    "exec",
    "function",
    "lambda",
    "python",
    "script",
}

# Tokens that may never appear inside the runtime-visible parts of the protocol
# (trigger predicates + action payload fields/prompt). The guard's *declaration*
# of forbidden categories is exempt (it is a denial list, not payload content).
DISALLOWED_ORACLE_TOKENS = {
    "gold_patch",
    "gold patch",
    "fix_diff",
    "official_eval",
    "resolved",
    "unresolved",
    "fail_to_pass",
    "pass_to_pass",
    "pass_to_fail",
    "test_patch",
    "oracle",
    # Amendment A / M2b — official test identity must never surface at runtime.
    "failing_test",
    "test_node",
    "test_oracle",
}


def _find_executable_keys(value: Any, *, path: str = "") -> list[str]:
    if isinstance(value, dict):
        matches: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in DISALLOWED_EXECUTABLE_KEYS:
                matches.append(child_path)
            matches.extend(_find_executable_keys(child, path=child_path))
        return matches
    if isinstance(value, (list, tuple)):
        matches = []
        for index, child in enumerate(value):
            matches.extend(_find_executable_keys(child, path=f"{path}[{index}]"))
        return matches
    return []


def find_oracle_tokens(value: Any, *, path: str = "") -> list[str]:
    """Scan runtime-visible content for oracle/answer leakage tokens."""
    matches: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            matches.extend(find_oracle_tokens(child, path=f"{path}.{key}" if path else str(key)))
        return matches
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            matches.extend(find_oracle_tokens(child, path=f"{path}[{index}]"))
        return matches
    if isinstance(value, str):
        lowered = value.lower()
        for token in sorted(DISALLOWED_ORACLE_TOKENS):
            if token in lowered:
                matches.append(f"{path}:{token}")
    return matches


@dataclass(frozen=True)
class ProtocolB1Trigger:
    type: str
    predicates: tuple[str, ...]
    evidence_sources: tuple[str, ...] = ("live_feature", "prefix_observation")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolB1Trigger":
        if data.get("type") != "live_feature_conjunction":
            raise ValueError("Protocol B1 only supports trigger.type=live_feature_conjunction")
        predicates = tuple(str(item) for item in data.get("predicates") or [])
        if not predicates:
            raise ValueError("Protocol B1 trigger.predicates is required")
        sources = tuple(str(item) for item in data.get("evidence_sources") or [])
        allowed_sources = {"live_feature", "prefix_observation"}
        if not sources or any(source not in allowed_sources for source in sources):
            raise ValueError("Protocol B1 trigger evidence must come from live features/prefix observations")
        leaks = find_oracle_tokens({"predicates": predicates, "sources": sources})
        if leaks:
            raise ValueError(f"Protocol B1 forbids oracle/answer tokens in trigger: {', '.join(leaks)}")
        return cls(type="live_feature_conjunction", predicates=predicates, evidence_sources=sources)


@dataclass(frozen=True)
class ProtocolB1Action:
    type: str
    info_kind: str
    payload_fields: tuple[str, ...]
    payload_provenance: str = ISSUE_TEXT_ONLY
    prompt_style: str = "observational_non_oracular"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolB1Action":
        if data.get("type") != "inject_deployable_information":
            raise ValueError("Protocol B1 only supports action.type=inject_deployable_information")
        info_kind = str(data.get("info_kind") or "")
        if info_kind not in INFO_KIND_ALLOWED_FIELDS:
            raise ValueError(f"unknown Protocol B1 info_kind: {info_kind}")
        allowed = INFO_KIND_ALLOWED_FIELDS[info_kind]
        fields = tuple(str(item) for item in data.get("payload_fields") or allowed)
        if not fields:
            raise ValueError("Protocol B1 action.payload_fields is required")
        illegal = sorted(set(fields) - set(allowed))
        if illegal:
            raise ValueError(
                f"Protocol B1 payload_fields not in deployable whitelist for {info_kind}: {', '.join(illegal)}"
            )
        # Amendment A / M2b: reproduction must be derived from the issue text only,
        # never from the official test (FAIL_TO_PASS / test_patch).
        provenance = str(data.get("payload_provenance") or ISSUE_TEXT_ONLY)
        if provenance != ISSUE_TEXT_ONLY:
            raise ValueError(
                f"Protocol B1 requires action.payload_provenance={ISSUE_TEXT_ONLY} (no official-test-derived repro)"
            )
        leaks = find_oracle_tokens(
            {"payload_fields": fields, "provenance": provenance, "prompt": data.get("prompt_style")}
        )
        if leaks:
            raise ValueError(f"Protocol B1 forbids oracle/answer tokens in action: {', '.join(leaks)}")
        prompt_style = str(data.get("prompt_style") or "observational_non_oracular")
        if prompt_style != "observational_non_oracular":
            raise ValueError("Protocol B1 prompt_style must be observational_non_oracular")
        return cls(
            type="inject_deployable_information",
            info_kind=info_kind,
            payload_fields=fields,
            payload_provenance=provenance,
            prompt_style=prompt_style,
        )


@dataclass(frozen=True)
class ProtocolB1Guard:
    max_injections_per_pair: int = 1
    replay_free: bool = True
    raw_payload_logging: bool = False
    oracle_capsule_allowed: bool = False
    official_eval_identifiers_runtime_visible: bool = False
    same_pair_positive_claim_allowed: bool = False
    uplift_claim_allowed: bool = False
    forbidden_payload_categories: tuple[str, ...] = tuple(sorted(REQUIRED_FORBIDDEN_CATEGORIES))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolB1Guard":
        if int(data.get("max_injections_per_pair") or 1) != 1:
            raise ValueError("Protocol B1 only supports max_injections_per_pair=1")
        if data.get("replay_free", True) is not True:
            raise ValueError("Protocol B1 requires guard.replay_free=true (task16 amendment A lineage)")
        if data.get("raw_payload_logging", False) is not False:
            raise ValueError("Protocol B1 requires guard.raw_payload_logging=false")
        if data.get("oracle_capsule_allowed", False) is not False:
            raise ValueError("Protocol B1 forbids oracle capsules (this is the B1 vs task16 line)")
        if data.get("official_eval_identifiers_runtime_visible", False) is not False:
            raise ValueError("Protocol B1 forbids official eval identifiers in runtime prompts")
        if data.get("same_pair_positive_claim_allowed", False) is not False:
            raise ValueError("Protocol B1 blocks same-pair positive attribution claims")
        if data.get("uplift_claim_allowed", False) is not False:
            raise ValueError("Protocol B1 is go/no-go only; uplift claims need the powered batch (B6)")
        forbidden = tuple(str(item) for item in data.get("forbidden_payload_categories") or sorted(REQUIRED_FORBIDDEN_CATEGORIES))
        missing = sorted(REQUIRED_FORBIDDEN_CATEGORIES - set(forbidden))
        if missing:
            raise ValueError(f"Protocol B1 guard must forbid leakage categories: {', '.join(missing)}")
        return cls(forbidden_payload_categories=tuple(sorted(set(forbidden))))


@dataclass(frozen=True)
class ProtocolB1Claim:
    allowed: str = PROTOCOL_B1_CLAIM_BOUNDARY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolB1Claim":
        allowed = str(data.get("allowed") or PROTOCOL_B1_CLAIM_BOUNDARY)
        if allowed != PROTOCOL_B1_CLAIM_BOUNDARY:
            raise ValueError(f"Protocol B1 only supports claim.allowed={PROTOCOL_B1_CLAIM_BOUNDARY}")
        return cls(allowed=allowed)


@dataclass(frozen=True)
class ProtocolB1:
    trigger: ProtocolB1Trigger
    action: ProtocolB1Action
    guard: ProtocolB1Guard = field(default_factory=ProtocolB1Guard)
    claim: ProtocolB1Claim = field(default_factory=ProtocolB1Claim)
    version: str = PROTOCOL_B1_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolB1":
        executable = _find_executable_keys(data)
        if executable:
            raise ValueError(f"Protocol B1 forbids executable fields: {', '.join(executable)}")
        version = str(data.get("version") or PROTOCOL_B1_VERSION)
        if version != PROTOCOL_B1_VERSION:
            raise ValueError(f"unsupported Protocol B1 version: {version}")
        return cls(
            version=version,
            trigger=ProtocolB1Trigger.from_dict(data.get("trigger") or {}),
            action=ProtocolB1Action.from_dict(data.get("action") or {}),
            guard=ProtocolB1Guard.from_dict(data.get("guard") or {}),
            claim=ProtocolB1Claim.from_dict(data.get("claim") or {}),
        )

    @property
    def protocol_hash(self) -> str:
        return stable_json_hash(self.to_dict())

    def runtime_visible(self) -> dict[str, Any]:
        """The parts an agent could see at runtime (trigger + action). The guard's
        forbidden-category *declaration* is excluded — it is a denial list, not
        payload content — so leakage scans do not false-positive on it."""
        # Keys deliberately avoid the reserved RAW_PAYLOAD_KEYS set (e.g. "action",
        # "prompt") so the no_raw_payload guard does not false-positive on a
        # legitimate plan projection.
        return {
            "trigger_view": {"predicates": list(self.trigger.predicates)},
            "injection_spec": {
                "info_kind": self.action.info_kind,
                "payload_fields": list(self.action.payload_fields),
                "payload_provenance": self.action.payload_provenance,
                "prompt_style": self.action.prompt_style,
            },
        }

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
                "info_kind": self.action.info_kind,
                "payload_fields": list(self.action.payload_fields),
                "payload_provenance": self.action.payload_provenance,
                "prompt_style": self.action.prompt_style,
            },
            "guard": {
                "max_injections_per_pair": self.guard.max_injections_per_pair,
                "replay_free": self.guard.replay_free,
                "raw_payload_logging": self.guard.raw_payload_logging,
                "oracle_capsule_allowed": self.guard.oracle_capsule_allowed,
                "official_eval_identifiers_runtime_visible": self.guard.official_eval_identifiers_runtime_visible,
                "same_pair_positive_claim_allowed": self.guard.same_pair_positive_claim_allowed,
                "uplift_claim_allowed": self.guard.uplift_claim_allowed,
                "forbidden_payload_categories": list(self.guard.forbidden_payload_categories),
            },
            "claim": {"allowed": self.claim.allowed},
        }


def protocol_b1_template(
    *,
    info_kind: str = "issue_text_reproduction",
    predicates: tuple[str, ...] = (
        "issue_text_contains_reproduction is true",
        "agent_reproduced_failure_from_issue is true",
        "about_to_emit_patch is true",
    ),
) -> ProtocolB1:
    return ProtocolB1.from_dict(
        {
            "version": PROTOCOL_B1_VERSION,
            "trigger": {
                "type": "live_feature_conjunction",
                "predicates": list(predicates),
                "evidence_sources": ["live_feature", "prefix_observation"],
            },
            "action": {
                "type": "inject_deployable_information",
                "info_kind": info_kind,
                "payload_fields": list(INFO_KIND_ALLOWED_FIELDS[info_kind]),
                "payload_provenance": ISSUE_TEXT_ONLY,
                "prompt_style": "observational_non_oracular",
            },
            "guard": {
                "max_injections_per_pair": 1,
                "replay_free": True,
                "raw_payload_logging": False,
                "oracle_capsule_allowed": False,
                "official_eval_identifiers_runtime_visible": False,
                "same_pair_positive_claim_allowed": False,
                "uplift_claim_allowed": False,
                "forbidden_payload_categories": sorted(REQUIRED_FORBIDDEN_CATEGORIES),
            },
            "claim": {"allowed": PROTOCOL_B1_CLAIM_BOUNDARY},
        }
    )


__all__ = [
    "INFO_KIND_ALLOWED_FIELDS",
    "PROTOCOL_B1_CLAIM_BOUNDARY",
    "PROTOCOL_B1_VERSION",
    "REQUIRED_FORBIDDEN_CATEGORIES",
    "ProtocolB1",
    "find_oracle_tokens",
    "protocol_b1_template",
]
