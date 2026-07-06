from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live import load_mapping_file
from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.hybrid_runner import HybridReplayGenerationModel
from wutai_clinic.intervention.protocol_v1 import ProtocolV1
from wutai_clinic.intervention.protocol_v1_hook import (
    ProtocolV1ConstraintHook,
    ProtocolV1ConstraintViolation,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_PHASE = "6.protocol_v1_sweagent_live_single"
SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_VERSION = "phase6_protocol_v1_sweagent_live_single_v1"
BOUNDARY = (
    "This package plans or executes one Protocol v1 SWE-agent live-single adapter arm. "
    "Execution requires explicit Docker and external-provider acknowledgements. A constraint "
    "block is reported as hook enforcement, not as an official outcome or uplift claim."
)

RunSingleFactory = Callable[[Path], Any]
ProtocolV1ArmType = Literal["control", "treatment"]


@dataclass(frozen=True)
class SWEAgentProtocolV1LiveSingleSpec:
    config_path: Path
    output_dir: Path
    protocol: ProtocolV1
    replay_actions: list[dict[str, Any] | str] = field(default_factory=list)
    arm_type: ProtocolV1ArmType = "treatment"
    execute: bool = False
    source_task_id: str | None = None
    pair_id: str | None = None
    require_official_eval: bool = False


@dataclass
class SWEAgentProtocolV1Attachment:
    hook: ProtocolV1ConstraintHook | None
    original_model: Any
    hybrid_model: HybridReplayGenerationModel
    permission_policy: RuntimePermissionPolicy

    def restore(self, run_single: Any) -> None:
        run_single.agent.model = self.original_model


class SWEAgentProtocolV1RunSingleAdapter:
    def __init__(self, *, policy: RuntimePermissionPolicy | None = None):
        self.policy = policy or RuntimePermissionPolicy()

    def assert_live_authorized(self, *, require_official_eval: bool = False) -> None:
        self.policy.assert_allows(
            require_docker=True,
            require_external_provider=True,
            require_official_eval=require_official_eval,
        )

    def attach(
        self,
        *,
        run_single: Any,
        protocol: ProtocolV1,
        source_task_id: str | None = None,
        pair_id: str | None = None,
        replay_actions: list[dict[str, Any] | str] | None = None,
        arm_type: ProtocolV1ArmType = "treatment",
        require_official_eval: bool = False,
    ) -> SWEAgentProtocolV1Attachment:
        self.assert_live_authorized(require_official_eval=require_official_eval)
        agent = getattr(run_single, "agent", None)
        if agent is None or not hasattr(agent, "add_hook"):
            raise RuntimeError("RunSingle agent must expose add_hook")
        original_model = getattr(agent, "model", None)
        if original_model is None or not hasattr(original_model, "query"):
            raise RuntimeError("RunSingle agent.model must expose query")
        replay_actions = list(replay_actions or [])
        hybrid_model = HybridReplayGenerationModel(
            replay_actions=replay_actions,
            delegate=original_model,
        )
        agent.model = hybrid_model
        hook = None
        if arm_type == "treatment":
            hook = ProtocolV1ConstraintHook(
                protocol=protocol,
                source_task_id=source_task_id,
                pair_id=pair_id,
                replay_prefix_action_count=len(replay_actions),
            )
            agent.add_hook(hook)
        return SWEAgentProtocolV1Attachment(
            hook=hook,
            original_model=original_model,
            hybrid_model=hybrid_model,
            permission_policy=self.policy,
        )


def _load_run_single_from_config(config_path: Path) -> Any:
    try:
        from sweagent.run.run_single import RunSingle, RunSingleConfig
    except Exception as exc:  # pragma: no cover - optional live dependency
        raise RuntimeError("SWE-agent run_single is required for execute=true") from exc

    payload = load_mapping_file(config_path)
    config = RunSingleConfig.model_validate(payload)
    return RunSingle.from_config(config)


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl" and path.is_file():
        with path.open("rb") as handle:
            record_count = sum(1 for line in handle if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": record_count,
        "exists": path.is_file(),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def run_sweagent_protocol_v1_live_single(
    *,
    spec: SWEAgentProtocolV1LiveSingleSpec,
    policy: RuntimePermissionPolicy,
    run_single_factory: RunSingleFactory | None = None,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    run_single_factory = run_single_factory or _load_run_single_from_config
    config_exists = spec.config_path.is_file()
    authorized = policy.allows(
        require_docker=True,
        require_external_provider=True,
        require_official_eval=spec.require_official_eval,
    )
    replay_ready = len(spec.replay_actions) > 0
    gates = {
        "config_path_exists": config_exists,
        "protocol_v1_valid": True,
        "replay_actions_present_if_execute": not spec.execute or replay_ready,
        "docker_ack_if_execute": not spec.execute or policy.allow_docker,
        "external_provider_ack_if_execute": not spec.execute or policy.allow_external_provider,
        "official_eval_ack_if_required": not spec.require_official_eval or policy.allow_official_eval,
        "official_eval_not_claimed": not spec.require_official_eval or policy.allow_official_eval,
        "same_pair_positive_claim_not_made": True,
        "raw_payload_logging_disabled": spec.protocol.guard.raw_payload_logging is False,
    }
    should_run = spec.execute and authorized and config_exists and replay_ready
    attachment = None
    run_error = None
    run_result_type = None
    constraint_block_event = None
    run_single_started = False

    if should_run:
        run_single = None
        try:
            run_single = run_single_factory(spec.config_path)
            adapter = SWEAgentProtocolV1RunSingleAdapter(policy=policy)
            attachment = adapter.attach(
                run_single=run_single,
                protocol=spec.protocol,
                source_task_id=spec.source_task_id,
                pair_id=spec.pair_id,
                replay_actions=spec.replay_actions,
                arm_type=spec.arm_type,
                require_official_eval=spec.require_official_eval,
            )
            run_single_started = True
            result = run_single.run()
            run_result_type = type(result).__name__
        except ProtocolV1ConstraintViolation as exc:
            constraint_block_event = exc.event
        except Exception as exc:  # pragma: no cover - environment-specific live failure path
            run_error = f"{type(exc).__name__}: {exc}"
        finally:
            if attachment is not None and run_single is not None:
                attachment.restore(run_single)

    hook_events = attachment.hook.audit_events if attachment is not None and attachment.hook else []
    gates.update(
        {
            "no_unrequested_run": spec.execute or not should_run,
            "run_started_if_execute": not spec.execute or run_single_started,
            "hook_attached_if_execute": not spec.execute or attachment is not None,
            "constraint_hook_attached_if_treatment_execute": (
                not spec.execute or spec.arm_type == "control" or bool(attachment and attachment.hook)
            ),
            "hook_events_present_if_treatment_execute": (
                not spec.execute or spec.arm_type == "control" or bool(hook_events)
            ),
            "run_error_absent": run_error is None,
        }
    )

    if not spec.execute:
        decision = "protocol_v1_live_single_planned_no_run"
    elif not authorized:
        decision = "protocol_v1_live_single_blocked_needs_ack"
    elif not config_exists:
        decision = "protocol_v1_live_single_blocked_missing_config"
    elif not replay_ready:
        decision = "protocol_v1_live_single_blocked_missing_replay_actions"
    elif run_error is not None:
        decision = "protocol_v1_live_single_run_failed"
    elif constraint_block_event is not None:
        decision = "protocol_v1_live_single_constraint_blocked"
    else:
        decision = "protocol_v1_live_single_run_completed"
    arm_complete = (
        spec.execute
        and run_single_started
        and run_error is None
        and constraint_block_event is None
    )
    if not spec.execute:
        recommended_next_step = "execute_control_and_treatment_arms_after_explicit_authorization"
    elif constraint_block_event is not None:
        recommended_next_step = "inspect_constraint_block_event_before_retry"
    elif run_error is not None:
        recommended_next_step = "fix_live_single_runtime_error_before_pair_assembly"
    elif not authorized:
        recommended_next_step = "collect_required_runtime_acknowledgements_before_execute"
    else:
        recommended_next_step = "assemble_protocol_v1_pair_if_both_arms_complete"

    protocol_path = spec.output_dir / "protocol_v1_live_single_protocol.json"
    replay_path = spec.output_dir / "protocol_v1_live_single_replay_actions.json"
    events_path = spec.output_dir / "protocol_v1_live_single_events.jsonl"
    report_path = spec.output_dir / "protocol_v1_live_single_report.json"
    manifest_path = spec.output_dir / "protocol_v1_live_single_manifest.json"
    _write_json(protocol_path, spec.protocol.to_dict())
    _write_json(replay_path, spec.replay_actions)
    model_events = attachment.hybrid_model.event_rows() if attachment is not None else []
    events = [
        {"event_type": "model_query", **event} for event in model_events
    ] + [
        {"event_type": "protocol_v1_constraint", **event} for event in hook_events
    ]
    write_jsonl(events_path, events)
    report = generate_report(
        phase=SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_VERSION,
            "claim_boundary": BOUNDARY,
            "source_task_id": spec.source_task_id,
            "pair_id": spec.pair_id,
            "arm_type": spec.arm_type,
            "execute_requested": spec.execute,
            "run_single_started": run_single_started,
            "replay_action_count": len(spec.replay_actions),
            "replay_config_hash": stable_json_hash(spec.replay_actions),
            "run_result_type": run_result_type,
            "run_error": run_error,
            "constraint_block_event": constraint_block_event,
            "hook_event_count": len(hook_events),
            "model_event_count": len(model_events),
            "constraint_blocked": constraint_block_event is not None,
            "official_eval_completed": False,
            "continuation_policy": {
                "allow_pair_assembly": arm_complete and all(gates.values()),
                "allow_protocol_v1_real_run": False,
                "allow_official_uplift_claim": False,
                "recommended_next_step": recommended_next_step,
            },
        },
    )
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_PHASE,
        report=report,
        artifacts=[
            _artifact(path)
            for path in [spec.config_path, protocol_path, replay_path, events_path, report_path]
        ],
    )
    manifest["version"] = SWEAGENT_PROTOCOL_V1_LIVE_SINGLE_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "events": events,
        "hook_events": hook_events,
        "model_events": model_events,
        "protocol_path": protocol_path,
        "replay_path": replay_path,
        "events_path": events_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }
