from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from wutai_clinic.adapters.base import (
    ForkArmRequest,
    ForkArmResult,
    ReadOnlyProbe,
    RuntimePermissionPolicy,
)
from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.hybrid_runner import (
    CapsuleBuildContext,
    CapsuleMaterializationHook,
    HybridReplayGenerationModel,
    message_prefix_hash,
)
from wutai_clinic.intervention.paired_fork import (
    CLAIM_BOUNDARY as PAIRED_FORK_CLAIM_BOUNDARY,
    default_generation_messages,
    default_protocol,
    default_replay_actions,
)
from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    paired_replay_effect_label,
    verify_fork_equivalence,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PREFLIGHT_VERSION = "phase5_sweagent_adapter_preflight_v1"
SWEAGENT_PREFLIGHT_PHASE = "5.sweagent_adapter_preflight"
DEFAULT_SWEAGENT_PREFLIGHT_TASK_ID = "phase5_sweagent_adapter_preflight_mock_task"
SWEAGENT_PREFLIGHT_CLAIM_BOUNDARY = (
    "This preflight package validates SWE-agent adapter wiring, capsule materialization, "
    "and treatment injection gating only. It does not start SWE-agent, Docker, an external "
    "provider, or official SWE-bench evaluation."
)
SWEAGENT_LIVE_PLAN_PHASE = "5.sweagent_run_single_live_plan"
SWEAGENT_LIVE_CLAIM_BOUNDARY = (
    "This live plan authorizes attaching the hybrid replay/generation wrapper to an existing "
    "SWE-agent RunSingle object only. It does not by itself call RunSingle.run(), start Docker, "
    "call a provider, or claim official evaluation uplift."
)


@dataclass
class SWEAgentPreflightStats:
    api_calls: int = 0

    def model_dump(self) -> dict[str, int]:
        return {"api_calls": self.api_calls}


class SWEAgentPreflightDelegate:
    """Deterministic stand-in for a live SWE-agent model provider."""

    def __init__(self, *, message: str):
        self.message = message
        self.calls: list[list[dict[str, Any]]] = []
        self.stats = SWEAgentPreflightStats()

    def query(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        self.stats.api_calls += 1
        self.calls.append(copy.deepcopy(history))
        return {"message": self.message}


@dataclass(frozen=True)
class MappingReadOnlyProbe:
    captures: dict[str, str] = field(default_factory=dict)

    def capture(self, command: str, *, cwd: str | None = None) -> str:
        key = f"{cwd or ''}::{command}"
        if key in self.captures:
            return self.captures[key]
        return self.captures.get(command, "")


class SWEEnvRuntimeProbe:
    """Read SWEEnv state through runtime.execute instead of the interactive shell session."""

    def __init__(self, *, env: Any, timeout: float | None = 30):
        self.env = env
        self.timeout = timeout
        self.command_hashes: list[str] = []

    def capture(self, command: str, *, cwd: str | None = None) -> str:
        try:
            from swerex.runtime.abstract import Command as RexCommand
        except Exception as exc:  # pragma: no cover - depends on optional SWE-agent install
            raise RuntimeError("swerex is required for SWEEnvRuntimeProbe") from exc

        runtime = getattr(getattr(self.env, "deployment", None), "runtime", None)
        if runtime is None or not hasattr(runtime, "execute"):
            raise RuntimeError("SWEEnvRuntimeProbe requires env.deployment.runtime.execute")

        self.command_hashes.append(stable_json_hash({"command": command, "cwd": cwd}))
        response = asyncio.run(
            runtime.execute(
                RexCommand(
                    command=command,
                    timeout=self.timeout,
                    shell=True,
                    check=False,
                    cwd=cwd,
                    merge_output_streams=False,
                )
            )
        )
        exit_code = getattr(response, "exit_code", 0)
        stdout = str(getattr(response, "stdout", ""))
        stderr = str(getattr(response, "stderr", ""))
        if exit_code not in (0, None):
            raise RuntimeError(
                f"read-only runtime probe failed with exit_code={exit_code}: {stderr[:200]}"
            )
        return stdout


@dataclass(frozen=True)
class SWEAgentCapsuleConfig:
    mode: str = "preflight"
    task_id: str = DEFAULT_SWEAGENT_PREFLIGHT_TASK_ID
    repo_hash: str = "sweagent_preflight_repo_hash"
    agent_config_hash: str = "sweagent_preflight_agent_config_hash"
    provider_config_hash: str = "sweagent_preflight_provider_config_hash"
    model_request_hash: str = "sweagent_preflight_model_request_hash"
    runner_config_hash: str = "sweagent_preflight_runner_config_hash"
    deployment_hash: str = "sweagent_preflight_deployment_hash"
    replay_config_hash: str = "sweagent_preflight_replay_config_hash"
    runtime_nondeterminism_policy: str = "preflight_single_thread_temperature_zero_no_docker"
    repo_cwd: str | None = None
    diff_command: str = "git diff --binary --no-ext-diff"
    observation_window: list[str] = field(default_factory=list)


class SWEAgentCapsuleExtractor:
    """Build StateCapsule hashes from a SWE-agent-shaped runtime boundary."""

    def __init__(
        self,
        *,
        probe: ReadOnlyProbe,
        config: SWEAgentCapsuleConfig | None = None,
    ):
        self.probe = probe
        self.config = config or SWEAgentCapsuleConfig()

    def build(
        self,
        context: CapsuleBuildContext,
        *,
        overrides: dict[str, str] | None = None,
    ) -> StateCapsule:
        diff_output = self.probe.capture(self.config.diff_command, cwd=self.config.repo_cwd)
        payload: dict[str, Any] = {
            "task_id": self.config.task_id,
            "repo_hash": stable_json_hash(self.config.repo_hash),
            "agent_config_hash": stable_json_hash(self.config.agent_config_hash),
            "provider_config_hash": stable_json_hash(self.config.provider_config_hash),
            "message_prefix_hash": message_prefix_hash(context.messages),
            "working_tree_diff_hash": stable_json_hash(diff_output),
            "observation_window_hash": stable_json_hash(self.config.observation_window),
            "model_request_hash": stable_json_hash(self.config.model_request_hash),
            "runner_config_hash": stable_json_hash(self.config.runner_config_hash),
            "deployment_hash": stable_json_hash(self.config.deployment_hash),
            "replay_config_hash": stable_json_hash(self.config.replay_config_hash),
            "runtime_nondeterminism_policy": self.config.runtime_nondeterminism_policy,
            "metadata": {
                "adapter": "sweagent",
                "mode": self.config.mode,
                "arm_type": context.arm_type,
                "agent_name": context.agent_name,
                "query_index": context.query_index,
                "diff_command_hash": stable_json_hash(self.config.diff_command),
                "repo_cwd_hash": stable_json_hash(self.config.repo_cwd or ""),
                "raw_probe_payload_logged": False,
            },
        }
        payload.update(overrides or {})
        return StateCapsule.from_dict(payload)


class SWEAgentForkRunner:
    """Preflight runner for the SWE-agent sequential replay fork adapter."""

    def __init__(
        self,
        *,
        probe: ReadOnlyProbe | None = None,
        capsule_config: SWEAgentCapsuleConfig | None = None,
        policy: RuntimePermissionPolicy | None = None,
    ):
        self.probe = probe or MappingReadOnlyProbe(
            {"git diff --binary --no-ext-diff": "sweagent_preflight_clean_diff"}
        )
        self.extractor = SWEAgentCapsuleExtractor(
            probe=self.probe,
            config=capsule_config or SWEAgentCapsuleConfig(),
        )
        self.policy = policy or RuntimePermissionPolicy()

    def run_arm(self, request: ForkArmRequest) -> ForkArmResult:
        if any(not passed for passed in self.policy.gate_results().values()):
            raise RuntimeError("SWEAgentForkRunner preflight does not execute live runtimes")

        delegate = SWEAgentPreflightDelegate(
            message=f"phase5_sweagent_{request.arm_type}_generation_placeholder"
        )
        model = HybridReplayGenerationModel(
            replay_actions=request.replay_actions,
            delegate=delegate,
        )
        agent = type("SWEAgentPreflightAgent", (), {"model": model})()

        def build_capsule(context: CapsuleBuildContext) -> StateCapsule:
            return self.extractor.build(context, overrides=request.capsule_overrides)

        hook = CapsuleMaterializationHook(
            arm_type=request.arm_type,
            protocol=request.protocol,
            capsule_builder=build_capsule,
            feature_extractor=lambda _context: request.features,
            reference_capsule=request.reference_capsule,
        )
        hook.on_init(agent=agent)

        replay_messages: list[dict[str, Any]] = []
        for _action in request.replay_actions:
            hook.on_model_query(messages=replay_messages, agent=f"{request.arm_type}_sweagent")
            model.query(replay_messages)

        live_messages = copy.deepcopy(request.generation_messages)
        hook.on_model_query(messages=live_messages, agent=f"{request.arm_type}_sweagent")
        generation_output = model.query(live_messages)
        if hook.capsule is None:
            raise RuntimeError("SWE-agent adapter preflight did not materialize a capsule")

        return ForkArmResult(
            arm_type=request.arm_type,
            capsule=hook.capsule,
            model_events=[
                {
                    "arm_type": request.arm_type,
                    "event_type": "model_query",
                    "adapter": "sweagent",
                    **event,
                }
                for event in model.event_rows()
            ],
            hook_events=[
                {
                    "arm_type": request.arm_type,
                    "event_type": "capsule_hook",
                    "adapter": "sweagent",
                    **event,
                }
                for event in hook.safe_audit_events
            ],
            generation_output=generation_output,
            delegate_call_count=delegate.stats.api_calls,
            metadata={"adapter": "sweagent", "mode": "preflight", **request.metadata},
        )


@dataclass
class SWEAgentRunSingleAttachment:
    arm_type: str
    original_model: Any
    hybrid_model: HybridReplayGenerationModel
    hook: CapsuleMaterializationHook
    capsule_config: SWEAgentCapsuleConfig
    permission_policy: RuntimePermissionPolicy

    def restore(self, run_single: Any) -> None:
        run_single.agent.model = self.original_model


class SWEAgentRunSingleAdapter:
    """Attach the hybrid fork machinery to an already constructed SWE-agent RunSingle."""

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
        request: ForkArmRequest,
        capsule_config: SWEAgentCapsuleConfig | None = None,
        require_official_eval: bool = False,
    ) -> SWEAgentRunSingleAttachment:
        self.assert_live_authorized(require_official_eval=require_official_eval)
        agent = getattr(run_single, "agent", None)
        env = getattr(run_single, "env", None)
        if agent is None or env is None:
            raise RuntimeError("RunSingle attachment requires run_single.agent and run_single.env")
        if not hasattr(agent, "add_hook"):
            raise RuntimeError("RunSingle agent must expose add_hook")
        original_model = getattr(agent, "model", None)
        if original_model is None or not hasattr(original_model, "query"):
            raise RuntimeError("RunSingle agent.model must expose query")

        config = capsule_config or SWEAgentCapsuleConfig(
            runtime_nondeterminism_policy="live_run_single_sequential_replay_temperature_zero"
        )
        repo_name = getattr(getattr(env, "repo", None), "repo_name", None)
        if config.repo_cwd is None and repo_name:
            config = replace(config, repo_cwd=f"/{repo_name}")
        if config.mode != "live":
            config = replace(config, mode="live")
        extractor = SWEAgentCapsuleExtractor(
            probe=SWEEnvRuntimeProbe(env=env),
            config=config,
        )
        hybrid_model = HybridReplayGenerationModel(
            replay_actions=request.replay_actions,
            delegate=original_model,
        )
        agent.model = hybrid_model

        def build_capsule(context: CapsuleBuildContext) -> StateCapsule:
            return extractor.build(context, overrides=request.capsule_overrides)

        hook = CapsuleMaterializationHook(
            arm_type=request.arm_type,
            protocol=request.protocol,
            capsule_builder=build_capsule,
            feature_extractor=lambda _context: request.features,
            reference_capsule=request.reference_capsule,
        )
        agent.add_hook(hook)
        return SWEAgentRunSingleAttachment(
            arm_type=request.arm_type,
            original_model=original_model,
            hybrid_model=hybrid_model,
            hook=hook,
            capsule_config=config,
            permission_policy=self.policy,
        )


def sweagent_live_plan_report(
    *,
    policy: RuntimePermissionPolicy,
    require_official_eval: bool = False,
) -> dict[str, Any]:
    gates = {
        "run_single_adapter_available": True,
        "requires_docker_ack": policy.allow_docker,
        "requires_external_provider_ack": policy.allow_external_provider,
        "requires_official_eval_ack": policy.allow_official_eval
        if require_official_eval
        else True,
        "run_single_not_started": True,
        "generalized_uplift_claim_not_made": True,
    }
    allowed = policy.allows(
        require_docker=True,
        require_external_provider=True,
        require_official_eval=require_official_eval,
    )
    return generate_report(
        phase=SWEAGENT_LIVE_PLAN_PHASE,
        decision="sweagent_run_single_live_authorized"
        if allowed
        else "sweagent_run_single_live_blocked_needs_ack",
        gate_results=gates,
        extras={
            "claim_boundary": SWEAGENT_LIVE_CLAIM_BOUNDARY,
            "requires_official_eval": require_official_eval,
            "next_step": (
                "Construct RunSingle.from_config, attach SWEAgentRunSingleAdapter, then call run() "
                "only inside a single-task live experiment."
            ),
        },
    )


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


def _decision(gates: dict[str, bool], effect_label: str) -> str:
    if not all(gates.values()):
        return "sweagent_adapter_preflight_blocked"
    if effect_label == "pending_or_incomplete":
        return "sweagent_adapter_preflight_ready_no_real_run"
    return "sweagent_adapter_preflight_mock_outcome_label_ready"


def run_sweagent_fork_preflight(
    *,
    output_dir: Path,
    protocol: InterventionProtocol | None = None,
    replay_actions: list[dict[str, Any] | str] | None = None,
    generation_messages: list[dict[str, Any]] | None = None,
    features: dict[str, Any] | None = None,
    probe: ReadOnlyProbe | None = None,
    capsule_config: SWEAgentCapsuleConfig | None = None,
    control_resolved: bool | None = None,
    treatment_resolved: bool | None = None,
    treatment_capsule_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol = protocol or default_protocol()
    replay_actions = replay_actions or default_replay_actions()
    generation_messages = generation_messages or default_generation_messages()
    features = features or {"error_streak": 3}
    runner = SWEAgentForkRunner(
        probe=probe,
        capsule_config=capsule_config,
        policy=RuntimePermissionPolicy(),
    )

    control = runner.run_arm(
        ForkArmRequest(
            arm_type="control",
            protocol=protocol,
            replay_actions=replay_actions,
            generation_messages=generation_messages,
            features=features,
        )
    )
    treatment = runner.run_arm(
        ForkArmRequest(
            arm_type="treatment",
            protocol=protocol,
            replay_actions=replay_actions,
            generation_messages=generation_messages,
            features=features,
            reference_capsule=control.capsule,
            capsule_overrides=treatment_capsule_overrides or {},
        )
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
        "adapter_imports_without_sweagent_runtime": True,
        "protocol_v0_valid": True,
        "control_capsule_materialized": control.capsule is not None,
        "treatment_capsule_materialized": treatment.capsule is not None,
        "fork_equivalence_passed": fork["passed"],
        "treatment_trigger_hit": treatment.trigger_hit,
        "treatment_injection_count_at_most_one": treatment.injection_count <= 1,
        "control_not_injected": control.injection_count == 0,
        "raw_probe_payload_not_logged": all(
            event.get("raw_payload_logged") is not True for event in events
        ),
        "generalized_uplift_claim_not_made": True,
        **runner.policy.gate_results(),
    }
    report = generate_report(
        phase=SWEAGENT_PREFLIGHT_PHASE,
        decision=_decision(gates, effect_label),
        gate_results=gates,
        extras={
            "version": SWEAGENT_PREFLIGHT_VERSION,
            "effect_label": effect_label,
            "claim_boundary": SWEAGENT_PREFLIGHT_CLAIM_BOUNDARY,
            "paired_fork_claim_boundary": PAIRED_FORK_CLAIM_BOUNDARY,
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

    protocol_path = output_dir / "sweagent_preflight_protocol.json"
    control_capsule_path = output_dir / "sweagent_preflight_control_capsule.json"
    treatment_capsule_path = output_dir / "sweagent_preflight_treatment_capsule.json"
    events_path = output_dir / "sweagent_preflight_events.jsonl"
    report_path = output_dir / "sweagent_preflight_report.json"
    manifest_path = output_dir / "sweagent_preflight_manifest.json"

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
        phase=SWEAGENT_PREFLIGHT_PHASE,
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
    manifest["version"] = SWEAGENT_PREFLIGHT_VERSION
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


__all__ = [
    "DEFAULT_SWEAGENT_PREFLIGHT_TASK_ID",
    "MappingReadOnlyProbe",
    "SWEAGENT_PREFLIGHT_PHASE",
    "SWEAGENT_PREFLIGHT_VERSION",
    "SWEAgentCapsuleConfig",
    "SWEAgentCapsuleExtractor",
    "SWEAgentForkRunner",
    "SWEAgentPreflightDelegate",
    "SWEAgentRunSingleAdapter",
    "SWEAgentRunSingleAttachment",
    "SWEEnvRuntimeProbe",
    "sweagent_live_plan_report",
    "run_sweagent_fork_preflight",
]
