from __future__ import annotations

import hashlib
import json
from typing import Any

PHASE315_HOOK_VERSION = "phase315_exact_paired_runner_hook_v1"
PHASE316_HOOK_VERSION = "phase316_live_feature_hook_dry_run_v1"
CONTROL_POLICY = "frozen_baseline_no_extra_intervention"
INTERVENTION_POLICIES = {
    "break_recurrence_and_replan",
    "error_observation_recovery",
    "insert_validation_checkpoint",
    "same_action_escape",
}
POLICY_TEXT = {
    "break_recurrence_and_replan": (
        "Phase 3.15 intervention: the trajectory is repeating. Stop the repeated action, "
        "summarize the current state briefly, choose a different diagnostic path, and continue only "
        "after a concrete next check is identified."
    ),
    "error_observation_recovery": (
        "Phase 3.15 intervention: the recent observation is error-like. Do not ignore it or submit. "
        "Classify the error, run the narrowest verification needed, and adjust the patch path before "
        "continuing."
    ),
    "insert_validation_checkpoint": (
        "Phase 3.15 intervention: pause before further edits or final submission. Run a narrow "
        "validation command or inspect the directly affected code path, then use that result to decide "
        "the next action."
    ),
    "same_action_escape": (
        "Phase 3.15 intervention: the same action family has repeated too long. Switch to a different "
        "information source or validation mode before issuing another similar command."
    ),
}
POLICY_REQUIRED_REASON_CODES = {
    "insert_validation_checkpoint": ["validation_gap_after_edit"],
    "break_recurrence_and_replan": [
        "recurrence_spike",
        "loop_or_duplicate_pattern",
        "same_action_family_streak",
    ],
    "error_observation_recovery": ["error_streak_or_error_observation"],
    "same_action_escape": ["same_action_family_streak"],
}


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MockAgent:
    def __init__(self, trajectory_length: int):
        self.trajectory = [{} for _ in range(trajectory_length)]


def safe_placeholder_message() -> dict[str, str]:
    return {"role": "user", "content": "phase315_dry_run_placeholder_no_raw_task_payload"}


class StaticPrefixHook:
    def __init__(
        self,
        trigger_index: int | dict[str, Any] | None = None,
        policy_message: str | None = None,
        *,
        bridge_row: dict[str, Any] | None = None,
        injected: bool = False,
    ):
        if isinstance(trigger_index, dict):
            bridge_row = trigger_index
            trigger_index = None
        self.bridge_row = bridge_row
        self.trigger_index = (
            int(trigger_index)
            if trigger_index is not None
            else self._bridge_trigger_index(bridge_row)
        )
        self.policy_message = policy_message or self._bridge_policy_message(bridge_row)
        self.injected = injected
        self.agent: Any | None = None
        self.safe_audit_events: list[dict[str, Any]] = []

    @staticmethod
    def _bridge_trigger_index(bridge_row: dict[str, Any] | None) -> int | None:
        if bridge_row is None or bridge_row.get("injection_trigger_after_prefix_index") is None:
            return None
        return int(bridge_row["injection_trigger_after_prefix_index"])

    @staticmethod
    def _bridge_policy_message(bridge_row: dict[str, Any] | None) -> str:
        if bridge_row is None:
            return ""
        policy_id = str(bridge_row.get("intervention_policy_id", ""))
        return POLICY_TEXT.get(policy_id, "")

    def _noop_hook(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("on_"):
            return self._noop_hook
        raise AttributeError(name)

    def on_init(self, *, agent: Any) -> None:
        self.agent = agent

    def maybe_inject(
        self, prefix_index: int, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if self.bridge_row is not None and self.bridge_row.get("inject_policy_message") is not True:
            return messages
        if self.injected or self.trigger_index is None or prefix_index < self.trigger_index:
            return messages
        self.injected = True
        return messages + [{"role": "system", "content": self.policy_message}]

    def on_model_query(self, *, messages: list[dict[str, str]], agent: str) -> None:
        if self.agent is None:
            raise RuntimeError("StaticPrefixHook.on_init must run before on_model_query")
        step_index = len(getattr(self.agent, "trajectory", []))
        before_count = len(messages)
        should_inject = self._should_inject_at_step(step_index)
        if should_inject:
            messages.append({"role": "system", "content": self.policy_message})
            self.injected = True
        if self.bridge_row is not None:
            self.safe_audit_events.append(
                self._audit_event(step_index, before_count, len(messages), should_inject)
            )

    def _should_inject_at_step(self, step_index: int) -> bool:
        if self.injected or self.trigger_index is None:
            return False
        if self.bridge_row is not None:
            return (
                self.bridge_row.get("inject_policy_message") is True
                and step_index == self.trigger_index
            )
        return step_index >= self.trigger_index

    def _audit_event(
        self,
        step_index: int,
        before_count: int,
        after_count: int,
        should_inject: bool,
    ) -> dict[str, Any]:
        assert self.bridge_row is not None
        return {
            "phase": "3.15",
            "hook_version": PHASE315_HOOK_VERSION,
            "arm_id": self.bridge_row["arm_id"],
            "arm_type": self.bridge_row["arm_type"],
            "source_task_id": self.bridge_row["source_task_id"],
            "intervention_policy_id": self.bridge_row["intervention_policy_id"],
            "step_index": step_index,
            "trigger_index": self.bridge_row.get("injection_trigger_after_prefix_index"),
            "injection_decision": "injected" if should_inject else "not_injected",
            "message_count_before": before_count,
            "message_count_after": after_count,
            "message_delta": after_count - before_count,
            "policy_message_template_id": self.bridge_row.get("policy_message_template_id"),
            "policy_message_template_sha256": self.bridge_row.get("policy_message_template_sha256"),
            "raw_payload_persistence": "forbidden",
            "runner_started": False,
            "model_call_started": False,
        }


class Phase315PolicyInjectionHook(StaticPrefixHook):
    def __init__(self, bridge_row: dict[str, Any]):
        super().__init__(bridge_row=bridge_row)


def run_phase315_hook_once(
    row: dict[str, Any],
    *,
    trajectory_length: int,
) -> tuple[list[dict[str, Any]], int]:
    hook = Phase315PolicyInjectionHook(row)
    hook.on_init(agent=MockAgent(trajectory_length))
    dry_messages = [safe_placeholder_message()]
    hook.on_model_query(messages=dry_messages, agent="phase315_dry_run_agent")
    return hook.safe_audit_events, len(dry_messages)


def dry_run_phase315_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    if row.get("arm_type") == "control":
        events, _ = run_phase315_hook_once(
            row, trajectory_length=int(row["candidate_prefix_index"])
        )
        return [
            {
                **events[-1],
                "dry_run_case": "control_at_candidate_prefix",
                "expected_injection": False,
                "case_passed": events[-1]["message_delta"] == 0,
            }
        ]

    trigger_index = int(row["injection_trigger_after_prefix_index"])
    before_events, _ = run_phase315_hook_once(row, trajectory_length=max(0, trigger_index - 1))
    hook = Phase315PolicyInjectionHook(row)
    hook.on_init(agent=MockAgent(trigger_index))
    dry_messages = [safe_placeholder_message()]
    hook.on_model_query(messages=dry_messages, agent="phase315_dry_run_agent")
    hook.on_model_query(messages=dry_messages, agent="phase315_dry_run_agent")
    trigger_events = hook.safe_audit_events
    return [
        {
            **before_events[-1],
            "dry_run_case": "intervention_before_trigger",
            "expected_injection": False,
            "case_passed": before_events[-1]["message_delta"] == 0,
        },
        {
            **trigger_events[0],
            "dry_run_case": "intervention_at_trigger",
            "expected_injection": True,
            "case_passed": trigger_events[0]["message_delta"] == 1,
        },
        {
            **trigger_events[1],
            "dry_run_case": "intervention_duplicate_trigger",
            "expected_injection": False,
            "case_passed": trigger_events[1]["message_delta"] == 0,
        },
    ]


def build_phase315_dry_run_events(bridge_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in sorted(bridge_plan, key=lambda item: int(item["execution_index"])):
        rows.extend(dry_run_phase315_row(row))
    return rows


class LiveFeatureHook:
    def __init__(
        self,
        feature_schema: dict[str, Any],
        trigger_condition: dict[str, Any],
        policy_message: str,
        injected: bool = False,
    ):
        self.feature_schema = feature_schema
        self.trigger_condition = trigger_condition
        self.policy_message = policy_message
        self.injected = injected

    def maybe_inject(
        self, features: dict[str, Any], messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if self.injected:
            return messages
        for key, expected in self.trigger_condition.items():
            if features.get(key) != expected:
                return messages
        self.injected = True
        return messages + [{"role": "system", "content": self.policy_message}]


def candidate_required_reason_overlap(row: dict[str, Any]) -> set[str]:
    policy_id = str(row.get("intervention_policy_id"))
    required = set(POLICY_REQUIRED_REASON_CODES.get(policy_id, []))
    candidate = set(row.get("candidate_reason_codes", []))
    return required & candidate


def candidate_reference_streak(row: dict[str, Any]) -> int:
    value = row.get("candidate_prefix_only_context", {}).get("same_action_family_streak", 1)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 4))


def derive_live_trigger_step(row: dict[str, Any]) -> int:
    static_prefix = max(1, int(row.get("candidate_static_prefix_index", 1)))
    overlap = candidate_required_reason_overlap(row)
    reference_streak = candidate_reference_streak(row)
    if "same_action_family_streak" in overlap:
        proposed = reference_streak
    elif {"recurrence_spike", "loop_or_duplicate_pattern"} & overlap:
        proposed = max(2, reference_streak)
    else:
        proposed = max(3, reference_streak)
    return max(1, min(proposed, static_prefix))


def synthetic_feature_window(row: dict[str, Any]) -> list[dict[str, Any]]:
    trigger_step = derive_live_trigger_step(row)
    static_prefix = max(1, int(row.get("candidate_static_prefix_index", trigger_step)))
    end_step = min(max(trigger_step + 1, 3), max(static_prefix, trigger_step + 1))
    reference_streak = candidate_reference_streak(row)
    reason_overlap = candidate_required_reason_overlap(row)
    candidate_context = row.get("candidate_prefix_only_context", {})
    validation_gap = int(candidate_context.get("validation_gap_steps", 0) or 0)
    windows = []
    for step_index in range(1, end_step + 1):
        progress = min(1.0, step_index / max(1, trigger_step))
        recurrence_like = bool({"recurrence_spike", "loop_or_duplicate_pattern"} & reason_overlap)
        same_family_like = "same_action_family_streak" in reason_overlap
        feature_snapshot = {
            "online_str_v1": round(0.18 + (0.44 * progress if recurrence_like else 0.05), 6),
            "rolling_str": round(0.2 + (0.42 * progress if recurrence_like else 0.04), 6),
            "duplicate_state_ratio": round(
                0.12 + (0.48 * progress if recurrence_like else 0.03), 6
            ),
            "same_action_family_streak": min(max(1, step_index), reference_streak),
            "hypothesis_shift_rate": round(0.04 * (1.0 - progress), 6),
            "validation_gap_after_edit": min(step_index, validation_gap),
            "step_count": step_index,
        }
        feature_flags = {
            "required_reason_overlap_present": bool(reason_overlap),
            "recurrence_signature_present": recurrence_like and step_index >= trigger_step,
            "duplicate_signature_present": (
                "loop_or_duplicate_pattern" in reason_overlap and step_index >= trigger_step
            ),
            "same_family_signature_present": same_family_like and step_index >= trigger_step,
            "candidate_state_signature_present": bool(row.get("candidate_state_class")),
        }
        windows.append(
            {
                "step_index": step_index,
                "synthetic_live_trigger_step": trigger_step,
                "feature_snapshot": feature_snapshot,
                "feature_flags": feature_flags,
            }
        )
    return windows


def phase316_policy_template_id(policy_id: str) -> str:
    return f"{policy_id}_phase316_live_feature_hook_v1"


def live_predicate_matches(row: dict[str, Any], feature_window: dict[str, Any]) -> bool:
    if row.get("exact_static_prefix_trigger_disabled") is not True:
        return False
    if row.get("recalibrated_trigger_mode") != "live_feature_signature_window":
        return False
    if not candidate_required_reason_overlap(row):
        return False
    flags = feature_window["feature_flags"]
    policy_id = row.get("intervention_policy_id")
    if policy_id == "break_recurrence_and_replan":
        return flags["candidate_state_signature_present"] and (
            flags["recurrence_signature_present"]
            or flags["duplicate_signature_present"]
            or flags["same_family_signature_present"]
        )
    if policy_id == "same_action_escape":
        return flags["same_family_signature_present"]
    if policy_id == "insert_validation_checkpoint":
        return bool(flags["required_reason_overlap_present"])
    if policy_id == "error_observation_recovery":
        return bool(flags["required_reason_overlap_present"])
    return False


class Phase316LiveFeaturePolicyHook:
    def __init__(self, candidate_row: dict[str, Any]):
        self.candidate_row = candidate_row
        self.injected = False
        self.safe_message_count = 1
        self.safe_audit_events: list[dict[str, Any]] = []

    def on_live_feature_window(self, feature_window: dict[str, Any]) -> None:
        before_count = self.safe_message_count
        predicate_matched = live_predicate_matches(self.candidate_row, feature_window)
        should_inject = predicate_matched and not self.injected
        if should_inject:
            self.injected = True
            self.safe_message_count += 1
        policy_id = str(self.candidate_row["intervention_policy_id"])
        reason_overlap = sorted(candidate_required_reason_overlap(self.candidate_row))
        feature_signature = stable_json_hash(
            {
                "pair_id": self.candidate_row["pair_id"],
                "step_index": feature_window["step_index"],
                "feature_snapshot": feature_window["feature_snapshot"],
                "feature_flags": feature_window["feature_flags"],
                "reason_overlap": reason_overlap,
            }
        )
        self.safe_audit_events.append(
            {
                "phase": "3.16",
                "hook_version": PHASE316_HOOK_VERSION,
                "dry_run_mode": "synthetic_prefix_only_live_feature_window",
                "pair_id": self.candidate_row["pair_id"],
                "arm_id": self.candidate_row["arm_id"],
                "source_task_id": self.candidate_row["source_task_id"],
                "source_family": self.candidate_row["source_family"],
                "intervention_policy_id": policy_id,
                "step_index": feature_window["step_index"],
                "candidate_static_prefix_index": self.candidate_row[
                    "candidate_static_prefix_index"
                ],
                "synthetic_live_trigger_step": feature_window["synthetic_live_trigger_step"],
                "trigger_basis": "live_feature_signature_window",
                "exact_static_prefix_trigger_disabled": self.candidate_row[
                    "exact_static_prefix_trigger_disabled"
                ],
                "reason_overlap": reason_overlap,
                "candidate_state_class_sha256": stable_json_hash(
                    self.candidate_row.get("candidate_state_class", {})
                ),
                "per_candidate_reference_streak": candidate_reference_streak(self.candidate_row),
                "feature_ids_evaluated": list(
                    self.candidate_row.get("phase314_feature_inputs", [])
                ),
                "feature_signature_sha256": feature_signature,
                "feature_snapshot": feature_window["feature_snapshot"],
                "feature_flags": feature_window["feature_flags"],
                "live_predicate_matched": predicate_matched,
                "injection_decision": "injected" if should_inject else "not_injected",
                "injection_count_after": 1 if self.injected else 0,
                "dry_message_count_before": before_count,
                "dry_message_count_after": self.safe_message_count,
                "dry_message_delta": self.safe_message_count - before_count,
                "policy_template_id": phase316_policy_template_id(policy_id)
                if should_inject
                else None,
                "policy_template_sha256": (
                    stable_json_hash(
                        {"policy_id": policy_id, "hook_version": PHASE316_HOOK_VERSION}
                    )
                    if should_inject
                    else None
                ),
                "raw_payload_persistence": "forbidden",
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "batch3_real_run_authorized": False,
            }
        )


def dry_run_phase316_candidate(row: dict[str, Any]) -> list[dict[str, Any]]:
    hook = Phase316LiveFeaturePolicyHook(row)
    for feature_window in synthetic_feature_window(row):
        hook.on_live_feature_window(feature_window)
    return hook.safe_audit_events


def build_phase316_dry_run_events(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in sorted(candidate_rows, key=lambda item: int(item["execution_index"])):
        rows.extend(dry_run_phase316_candidate(row))
    return rows
