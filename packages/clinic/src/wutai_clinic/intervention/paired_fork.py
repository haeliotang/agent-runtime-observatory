from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.hybrid_runner import (
    CapsuleBuildContext,
    CapsuleMaterializationHook,
    HybridReplayGenerationModel,
    message_prefix_hash,
)
from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    paired_replay_effect_label,
    verify_fork_equivalence,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PAIRED_FORK_DRY_RUN_VERSION = "phase4_task7_paired_fork_dry_run_v1"
DEFAULT_TASK_ID = "phase4_task7_mock_capsule_equivalent_fork"
CLAIM_BOUNDARY = (
    "This dry-run package validates capsule-equivalent sequential replay fork wiring only. "
    "It does not start Docker, call an external provider, run official eval, or create a "
    "generalized causal-uplift claim."
)


@dataclass
class StaticDelegateStats:
    api_calls: int = 0

    def model_dump(self) -> dict[str, int]:
        return {"api_calls": self.api_calls}


class StaticDelegateModel:
    def __init__(self, *, message: str):
        self.message = message
        self.calls: list[list[dict[str, Any]]] = []
        self.stats = StaticDelegateStats()

    def query(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        self.stats.api_calls += 1
        self.calls.append(copy.deepcopy(history))
        return {"message": self.message}


@dataclass
class PairedForkArmResult:
    arm_type: str
    capsule: StateCapsule
    model_events: list[dict[str, Any]]
    hook_events: list[dict[str, Any]]
    generation_output: dict[str, Any]
    delegate_call_count: int

    @property
    def injection_count(self) -> int:
        return sum(1 for event in self.hook_events if event.get("injected") is True)

    @property
    def trigger_hit(self) -> bool:
        return any(event.get("trigger_hit") is True for event in self.hook_events)


def default_protocol() -> InterventionProtocol:
    return InterventionProtocol.from_dict(
        {
            "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
            "action": {
                "type": "inject_system_prompt",
                "message_id": "break_recurrence_and_replan",
            },
            "guard": {"debounce": "once_per_pair", "raw_payload_logging": False},
            "claim": {"allowed": "bounded_next_step_control"},
        }
    )


def default_replay_actions() -> list[dict[str, str]]:
    return [{"message": "phase4_task7_replay_action_placeholder_no_raw_payload"}]


def default_generation_messages() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "phase4_task7_task_placeholder_no_raw_payload"},
        {"role": "assistant", "content": "phase4_task7_replay_action_placeholder_no_raw_payload"},
        {"role": "tool", "content": "phase4_task7_observation_placeholder_no_raw_payload"},
    ]


def default_capsule_payload(messages: list[dict[str, Any]]) -> dict[str, str]:
    return {
        "task_id": DEFAULT_TASK_ID,
        "repo_hash": stable_json_hash("mock_repo_hash"),
        "agent_config_hash": stable_json_hash("mock_agent_config"),
        "provider_config_hash": stable_json_hash("mock_provider_config"),
        "message_prefix_hash": message_prefix_hash(messages),
        "working_tree_diff_hash": stable_json_hash("mock_working_tree_diff"),
        "observation_window_hash": stable_json_hash("mock_observation_window"),
        "model_request_hash": stable_json_hash("mock_model_request"),
        "runner_config_hash": stable_json_hash("mock_runner_config"),
        "deployment_hash": stable_json_hash("mock_deployment"),
        "replay_config_hash": stable_json_hash("mock_replay_config"),
        "runtime_nondeterminism_policy": "mock_single_worker_temperature_zero_no_docker",
    }


def _capsule_builder(base_payload: dict[str, str], overrides: dict[str, str] | None = None) -> Any:
    def build(context: CapsuleBuildContext) -> StateCapsule:
        payload = dict(base_payload)
        payload.update(overrides or {})
        payload["message_prefix_hash"] = message_prefix_hash(context.messages)
        payload["metadata"] = {
            "arm_type": context.arm_type,
            "agent_name": context.agent_name,
            "query_index": context.query_index,
            "dry_run": True,
        }
        return StateCapsule.from_dict(payload)

    return build


def _run_arm(
    *,
    arm_type: str,
    protocol: InterventionProtocol,
    replay_actions: list[dict[str, Any] | str],
    generation_messages: list[dict[str, Any]],
    features: dict[str, Any],
    reference_capsule: StateCapsule | None = None,
    capsule_overrides: dict[str, str] | None = None,
) -> PairedForkArmResult:
    delegate = StaticDelegateModel(message=f"phase4_task7_{arm_type}_generation_placeholder")
    model = HybridReplayGenerationModel(replay_actions=replay_actions, delegate=delegate)
    agent = type("DryRunAgent", (), {"model": model})()
    capsule_payload = default_capsule_payload(generation_messages)
    hook = CapsuleMaterializationHook(
        arm_type=arm_type,  # type: ignore[arg-type]
        protocol=protocol,
        capsule_builder=_capsule_builder(capsule_payload, capsule_overrides),
        feature_extractor=lambda _context: features,
        reference_capsule=reference_capsule,
    )
    hook.on_init(agent=agent)

    replay_messages: list[dict[str, Any]] = []
    for _action in replay_actions:
        hook.on_model_query(messages=replay_messages, agent=f"{arm_type}_arm")
        model.query(replay_messages)

    live_messages = copy.deepcopy(generation_messages)
    hook.on_model_query(messages=live_messages, agent=f"{arm_type}_arm")
    generation_output = model.query(live_messages)
    if hook.capsule is None:
        raise RuntimeError("capsule was not materialized at first generation query")
    return PairedForkArmResult(
        arm_type=arm_type,
        capsule=hook.capsule,
        model_events=[
            {"arm_type": arm_type, "event_type": "model_query", **event}
            for event in model.event_rows()
        ],
        hook_events=[
            {"arm_type": arm_type, "event_type": "capsule_hook", **event}
            for event in hook.safe_audit_events
        ],
        generation_output=generation_output,
        delegate_call_count=delegate.stats.api_calls,
    )


def _decision(gates: dict[str, bool], effect_label: str) -> str:
    if not all(gates.values()):
        return "paired_fork_dry_run_blocked"
    if effect_label == "pending_or_incomplete":
        return "paired_fork_dry_run_ready_no_official_eval"
    return "paired_fork_dry_run_mock_outcome_label_ready"


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl":
        with path.open("rb") as handle:
            record_count = sum(1 for line in handle if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "record_count": record_count,
    }


def run_paired_fork_dry_run(
    *,
    output_dir: Path,
    protocol: InterventionProtocol | None = None,
    replay_actions: list[dict[str, Any] | str] | None = None,
    generation_messages: list[dict[str, Any]] | None = None,
    features: dict[str, Any] | None = None,
    control_resolved: bool | None = None,
    treatment_resolved: bool | None = None,
    treatment_capsule_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol = protocol or default_protocol()
    replay_actions = replay_actions or default_replay_actions()
    generation_messages = generation_messages or default_generation_messages()
    features = features or {"error_streak": 3}

    control = _run_arm(
        arm_type="control",
        protocol=protocol,
        replay_actions=replay_actions,
        generation_messages=generation_messages,
        features=features,
    )
    treatment = _run_arm(
        arm_type="treatment",
        protocol=protocol,
        replay_actions=replay_actions,
        generation_messages=generation_messages,
        features=features,
        reference_capsule=control.capsule,
        capsule_overrides=treatment_capsule_overrides,
    )
    fork = verify_fork_equivalence(control.capsule, treatment.capsule)
    effect_label = paired_replay_effect_label(
        fork_equivalence=fork,
        trigger_hit=treatment.trigger_hit,
        injection_count=treatment.injection_count,
        control_resolved=control_resolved,
        treatment_resolved=treatment_resolved,
    )
    events = [
        *control.model_events,
        *control.hook_events,
        *treatment.model_events,
        *treatment.hook_events,
    ]
    gates = {
        "protocol_v0_valid": True,
        "control_capsule_materialized": control.capsule is not None,
        "treatment_capsule_materialized": treatment.capsule is not None,
        "fork_equivalence_passed": fork["passed"],
        "treatment_injection_count_at_most_one": treatment.injection_count <= 1,
        "treatment_trigger_hit": treatment.trigger_hit,
        "control_not_injected": control.injection_count == 0,
        "raw_payload_not_logged": all(
            event.get("raw_payload_logged") is not True for event in events
        ),
        "external_provider_not_called": True,
        "docker_not_started": True,
        "official_eval_not_claimed": True,
        "generalized_uplift_claim_not_made": True,
    }
    report = generate_report(
        phase="4.task7.paired_fork_dry_run",
        decision=_decision(gates, effect_label),
        gate_results=gates,
        extras={
            "version": PAIRED_FORK_DRY_RUN_VERSION,
            "effect_label": effect_label,
            "claim_boundary": CLAIM_BOUNDARY,
            "fork_equivalence": fork,
            "protocol_hash": protocol.protocol_hash,
            "control_capsule_fingerprint": control.capsule.fingerprint,
            "treatment_capsule_fingerprint": treatment.capsule.fingerprint,
            "control_generation_output_sha256": stable_json_hash(control.generation_output),
            "treatment_generation_output_sha256": stable_json_hash(treatment.generation_output),
            "control_delegate_call_count": control.delegate_call_count,
            "treatment_delegate_call_count": treatment.delegate_call_count,
            "mock_outcome_used": control_resolved is not None or treatment_resolved is not None,
            "replay_action_count": len(replay_actions),
            "generation_message_prefix_hash": message_prefix_hash(generation_messages),
        },
    )

    protocol_path = output_dir / "paired_fork_protocol.json"
    control_capsule_path = output_dir / "paired_fork_control_capsule.json"
    treatment_capsule_path = output_dir / "paired_fork_treatment_capsule.json"
    events_path = output_dir / "paired_fork_events.jsonl"
    report_path = output_dir / "paired_fork_report.json"
    manifest_path = output_dir / "paired_fork_manifest.json"

    protocol_path.write_text(
        json.dumps(protocol.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    control_capsule_path.write_text(
        json.dumps(control.capsule.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    treatment_capsule_path.write_text(
        json.dumps(treatment.capsule.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    write_jsonl(events_path, events)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = generate_manifest(
        phase="4.task7.paired_fork_dry_run",
        report=report,
        artifacts=[
            _artifact(path)
            for path in [
                protocol_path,
                control_capsule_path,
                treatment_capsule_path,
                events_path,
                report_path,
            ]
        ],
    )
    manifest["version"] = PAIRED_FORK_DRY_RUN_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "protocol_path": protocol_path,
        "control_capsule_path": control_capsule_path,
        "treatment_capsule_path": treatment_capsule_path,
        "events_path": events_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }
