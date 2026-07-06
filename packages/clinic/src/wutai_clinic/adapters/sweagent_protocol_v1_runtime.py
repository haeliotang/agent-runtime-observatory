from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wutai_clinic.adapters.sweagent_live import load_mapping_file
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_PHASE = "6.protocol_v1_sweagent_runtime_config"
SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_VERSION = "phase6_protocol_v1_runtime_config_v1"
BOUNDARY = (
    "This package activates a SWE-agent RunSingle config for one Protocol v1 arm. "
    "It writes execution budget and arm-scoped output paths, but it never writes provider secrets "
    "and never starts Docker or calls a model provider."
)

ProtocolV1ArmType = Literal["control", "treatment"]


@dataclass(frozen=True)
class SWEAgentProtocolV1RuntimeConfigSpec:
    config_path: Path
    output_dir: Path
    arm_type: ProtocolV1ArmType
    native_output_dir: Path | None = None
    model_name: str | None = None
    api_base: str | None = None
    per_instance_call_limit: int = 20
    per_instance_cost_limit: float = 0.0
    total_cost_limit: float = 0.0
    provider_key_env: str = "OPENAI_API_KEY"
    provider_api_base_env: str = "OPENAI_API_BASE"
    source_task_id: str | None = None
    pair_id: str | None = None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": None,
        "exists": path.is_file(),
    }


def _model_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    agent = payload.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("RunSingle config agent must be a mapping")
    model = agent.setdefault("model", {})
    if not isinstance(model, dict):
        raise ValueError("RunSingle config agent.model must be a mapping")
    return model


def _default_native_output_dir(spec: SWEAgentProtocolV1RuntimeConfigSpec) -> Path:
    return spec.output_dir / "native_run"


def _arm_scoped(path: Path, arm_type: str) -> bool:
    return arm_type in path.parts or path.name == arm_type


def activate_sweagent_protocol_v1_runtime_config(
    spec: SWEAgentProtocolV1RuntimeConfigSpec,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    config_path = spec.output_dir / "protocol_v1_runtime_config.json"
    report_path = spec.output_dir / "protocol_v1_runtime_config_report.json"
    manifest_path = spec.output_dir / "protocol_v1_runtime_config_manifest.json"

    config_exists = spec.config_path.is_file()
    loaded_mapping = False
    activated_config: dict[str, Any] | None = None
    input_api_key_present = False
    output_api_key_value = None
    model_name = spec.model_name
    native_output_dir = spec.native_output_dir or _default_native_output_dir(spec)
    native_output_dir = native_output_dir.resolve()

    if config_exists:
        loaded = load_mapping_file(spec.config_path)
        if not isinstance(loaded, dict):
            raise ValueError("RunSingle config must be a mapping")
        loaded_mapping = True
        activated_config = copy.deepcopy(loaded)
        model = _model_mapping(activated_config)
        input_api_key_present = bool(model.get("api_key"))
        if spec.model_name:
            model["name"] = spec.model_name
        model_name = str(model.get("name") or "")
        model["per_instance_call_limit"] = spec.per_instance_call_limit
        model["per_instance_cost_limit"] = spec.per_instance_cost_limit
        model["total_cost_limit"] = spec.total_cost_limit
        if spec.api_base is not None:
            model["api_base"] = spec.api_base
        model["api_key"] = None
        output_api_key_value = model.get("api_key")
        activated_config["output_dir"] = native_output_dir.as_posix()
        _write_json(config_path, activated_config)

    api_base_configured = bool(
        spec.api_base
        or (
            isinstance(activated_config, dict)
            and isinstance(activated_config.get("agent"), dict)
            and isinstance(activated_config["agent"].get("model"), dict)
            and activated_config["agent"]["model"].get("api_base")
        )
    )
    gates = {
        "config_path_exists": config_exists,
        "config_loaded_mapping": loaded_mapping,
        "arm_type_valid": spec.arm_type in {"control", "treatment"},
        "arm_scoped_native_output_dir": _arm_scoped(native_output_dir, spec.arm_type),
        "per_instance_call_limit_positive": spec.per_instance_call_limit > 0,
        "cost_limits_non_negative": spec.per_instance_cost_limit >= 0 and spec.total_cost_limit >= 0,
        "provider_secret_not_written": output_api_key_value in {None, ""},
        "docker_not_started": True,
        "external_provider_not_called": True,
    }
    if not config_exists:
        decision = "protocol_v1_runtime_config_blocked_missing_config"
    elif all(gates.values()):
        decision = "protocol_v1_runtime_config_ready"
    else:
        decision = "protocol_v1_runtime_config_not_ready"

    report = generate_report(
        phase=SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_VERSION,
            "claim_boundary": BOUNDARY,
            "source_task_id": spec.source_task_id,
            "pair_id": spec.pair_id,
            "arm_type": spec.arm_type,
            "model_name": model_name,
            "native_output_dir": native_output_dir.as_posix(),
            "activated_config": config_path.as_posix() if config_path.is_file() else None,
            "per_instance_call_limit": spec.per_instance_call_limit,
            "per_instance_cost_limit": spec.per_instance_cost_limit,
            "total_cost_limit": spec.total_cost_limit,
            "api_base_configured": api_base_configured,
            "api_key_field_present_in_input": input_api_key_present,
            "provider_env_contract": {
                "api_key_env": spec.provider_key_env,
                "api_base_env": spec.provider_api_base_env,
                "api_key_env_present": bool(os.environ.get(spec.provider_key_env)),
                "api_base_env_present": bool(os.environ.get(spec.provider_api_base_env)),
                "secrets_persisted": False,
            },
            "continuation_policy": {
                "allow_protocol_v1_real_run": all(gates.values()),
                "allow_official_uplift_claim": False,
                "recommended_next_step": (
                    "run_sweagent_protocol_v1_live_single_execute_for_this_arm"
                    if all(gates.values())
                    else "fix_runtime_config_activation_gates"
                ),
            },
        },
    )
    _write_json(report_path, report)
    artifacts = [_artifact(spec.config_path)]
    if config_path.is_file():
        artifacts.append(_artifact(config_path))
    artifacts.append(_artifact(report_path))
    manifest = generate_manifest(
        phase=SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_PROTOCOL_V1_RUNTIME_CONFIG_VERSION
    _write_json(manifest_path, manifest)
    return {
        "config_path": config_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "report": report,
        "manifest": manifest,
    }
