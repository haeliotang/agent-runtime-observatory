from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from wutai_clinic.adapters.base import ForkArmRequest, RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent import (
    SWEAGENT_LIVE_CLAIM_BOUNDARY,
    SWEAgentCapsuleConfig,
    SWEAgentRunSingleAdapter,
)
from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.hybrid_runner import ArmType
from wutai_clinic.intervention.paired_fork import default_protocol, default_replay_actions
from wutai_clinic.intervention.replay_protocol import InterventionProtocol, StateCapsule
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_LIVE_SINGLE_PHASE = "5.sweagent_run_single_live_single"
SWEAGENT_LIVE_SINGLE_VERSION = "phase5_sweagent_run_single_live_single_v1"
SWEAGENT_LIVE_SINGLE_BOUNDARY = (
    "This package plans or executes one guarded SWE-agent RunSingle arm. A real run requires "
    "execute=true plus Docker and external-provider acknowledgements. The package does not run "
    "paired attribution or official evaluation by default."
)

RunSingleFactory = Callable[[Path], Any]


@dataclass(frozen=True)
class SWEAgentLiveSingleSpec:
    config_path: Path
    output_dir: Path
    arm_type: ArmType = "control"
    execute: bool = False
    protocol: InterventionProtocol = field(default_factory=default_protocol)
    replay_actions: list[dict[str, Any] | str] = field(default_factory=default_replay_actions)
    features: dict[str, Any] = field(default_factory=lambda: {"error_streak": 3})
    reference_capsule: StateCapsule | None = None
    capsule_config: SWEAgentCapsuleConfig | None = None
    require_official_eval: bool = False


def load_mapping_file(path: Path) -> Any:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(path.read_text())
    raise ValueError("expected a .json, .yaml, or .yml file")


def load_replay_actions(path: Path | None) -> list[dict[str, Any] | str]:
    if path is None:
        return default_replay_actions()
    data = load_mapping_file(path)
    if not isinstance(data, list):
        raise ValueError("replay actions must be a list")
    for action in data:
        if not isinstance(action, (dict, str)):
            raise ValueError("each replay action must be a string or mapping")
    return data


def load_features(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"error_streak": 3}
    data = load_mapping_file(path)
    if not isinstance(data, dict):
        raise ValueError("features must be a mapping")
    return data


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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _default_capsule_config(spec: SWEAgentLiveSingleSpec) -> SWEAgentCapsuleConfig:
    config_bytes = spec.config_path.read_bytes() if spec.config_path.exists() else b""
    return SWEAgentCapsuleConfig(
        mode="live",
        task_id="sweagent_live_single_pair",
        agent_config_hash=stable_json_hash(config_bytes.decode("utf-8", errors="replace")),
        provider_config_hash=stable_json_hash("provider_config_redacted"),
        model_request_hash=stable_json_hash(
            {
                "protocol_hash": spec.protocol.protocol_hash,
                "replay_action_count": len(spec.replay_actions),
            }
        ),
        runner_config_hash=stable_json_hash(
            {
                "config_path": spec.config_path.as_posix(),
                "execute": spec.execute,
                "require_official_eval": spec.require_official_eval,
            }
        ),
        deployment_hash=stable_json_hash("deployment_resolved_by_sweagent_run_single"),
        replay_config_hash=stable_json_hash(spec.replay_actions),
        runtime_nondeterminism_policy="live_run_single_sequential_replay_temperature_zero",
    )


def _safe_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return cleaned.strip("._") or "unknown"


def _config_value(payload: Any, dotted_path: tuple[str, ...]) -> Any:
    current = payload
    for key in dotted_path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _native_output_metadata(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        return {
            "source_task_id": None,
            "native_output_dir": None,
            "native_task_dir": None,
            "config_loaded": False,
        }
    payload = load_mapping_file(config_path)
    source_task_id = (
        _config_value(payload, ("problem_statement", "id"))
        or _config_value(payload, ("problem_statement", "instance_id"))
        or _config_value(payload, ("instance", "id"))
        or _config_value(payload, ("instance_id",))
    )
    output_dir = (
        _config_value(payload, ("output_dir",))
        or _config_value(payload, ("run", "output_dir"))
        or _config_value(payload, ("sweagent", "output_dir"))
    )
    native_output_dir = Path(str(output_dir)).expanduser() if output_dir else None
    native_task_dir = (
        native_output_dir / str(source_task_id)
        if native_output_dir is not None and source_task_id
        else None
    )
    return {
        "source_task_id": str(source_task_id) if source_task_id else None,
        "native_output_dir": native_output_dir.as_posix() if native_output_dir else None,
        "native_task_dir": native_task_dir.as_posix() if native_task_dir else None,
        "config_loaded": True,
    }


def _model_patch_from_pred(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    model_patch = payload.get("model_patch") if isinstance(payload, dict) else None
    return model_patch if isinstance(model_patch, str) else None


def _archive_native_patch_artifacts(*, config_path: Path, output_dir: Path) -> dict[str, Any]:
    metadata = _native_output_metadata(config_path)
    source_task_id = metadata["source_task_id"]
    native_task_dir = Path(metadata["native_task_dir"]) if metadata["native_task_dir"] else None
    archive_prefix = "sweagent_live_single"
    archived_patch = output_dir / f"{archive_prefix}.patch"
    archived_pred = output_dir / f"{archive_prefix}.pred"
    archived_traj = output_dir / f"{archive_prefix}.traj"
    source_patch = (
        native_task_dir / f"{source_task_id}.patch"
        if native_task_dir is not None and source_task_id
        else None
    )
    source_pred = (
        native_task_dir / f"{source_task_id}.pred"
        if native_task_dir is not None and source_task_id
        else None
    )
    source_traj = (
        native_task_dir / f"{source_task_id}.traj"
        if native_task_dir is not None and source_task_id
        else None
    )

    patch_source_kind = "missing"
    if source_patch is not None and source_patch.is_file():
        shutil.copyfile(source_patch, archived_patch)
        patch_source_kind = "native_patch_file"
    elif source_pred is not None:
        model_patch = _model_patch_from_pred(source_pred)
        if model_patch is not None:
            archived_patch.write_text(model_patch, encoding="utf-8")
            patch_source_kind = "native_pred_model_patch"

    if source_pred is not None and source_pred.is_file():
        shutil.copyfile(source_pred, archived_pred)
    if source_traj is not None and source_traj.is_file():
        shutil.copyfile(source_traj, archived_traj)

    patch_artifact = _artifact(archived_patch)
    pred_artifact = _artifact(archived_pred)
    traj_artifact = _artifact(archived_traj)
    return {
        **metadata,
        "patch_archived": archived_patch.is_file(),
        "patch_archive_path": archived_patch.as_posix(),
        "patch_archive_sha256": patch_artifact["sha256"],
        "patch_source_kind": patch_source_kind,
        "patch_source_path": source_patch.as_posix() if source_patch is not None else None,
        "prediction_archived": archived_pred.is_file(),
        "prediction_archive_path": archived_pred.as_posix(),
        "prediction_archive_sha256": pred_artifact["sha256"],
        "trajectory_archived": archived_traj.is_file(),
        "trajectory_archive_path": archived_traj.as_posix(),
        "trajectory_archive_sha256": traj_artifact["sha256"],
        "artifacts": [patch_artifact, pred_artifact, traj_artifact],
    }


def run_sweagent_live_single(
    *,
    spec: SWEAgentLiveSingleSpec,
    policy: RuntimePermissionPolicy,
    run_single_factory: RunSingleFactory | None = None,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    run_single_factory = run_single_factory or _load_run_single_from_config
    capsule_config = spec.capsule_config or _default_capsule_config(spec)
    request = ForkArmRequest(
        arm_type=spec.arm_type,
        protocol=spec.protocol,
        replay_actions=spec.replay_actions,
        generation_messages=[],
        features=spec.features,
        reference_capsule=spec.reference_capsule,
    )

    config_exists = spec.config_path.exists()
    authorized = policy.allows(
        require_docker=True,
        require_external_provider=True,
        require_official_eval=spec.require_official_eval,
    )
    treatment_reference_ready = spec.arm_type == "control" or spec.reference_capsule is not None
    replay_ready = True
    gates = {
        "config_path_exists": config_exists,
        "protocol_v0_valid": True,
        "replay_actions_valid_for_arm": replay_ready,
        "zero_replay_prefix_allowed": len(spec.replay_actions) >= 0,
        "reference_capsule_if_treatment_execute": not spec.execute or treatment_reference_ready,
        "docker_ack_if_execute": not spec.execute or policy.allow_docker,
        "external_provider_ack_if_execute": not spec.execute or policy.allow_external_provider,
        "official_eval_ack_if_required": not spec.require_official_eval
        or policy.allow_official_eval,
        "official_eval_not_claimed": not spec.require_official_eval or policy.allow_official_eval,
        "generalized_uplift_claim_not_made": True,
    }
    should_run = (
        spec.execute and authorized and config_exists and treatment_reference_ready and replay_ready
    )
    attachment = None
    run_error = None
    run_result_type = None

    if should_run:
        run_single = None
        try:
            run_single = run_single_factory(spec.config_path)
            adapter = SWEAgentRunSingleAdapter(policy=policy)
            attachment = adapter.attach(
                run_single=run_single,
                request=request,
                capsule_config=capsule_config,
                require_official_eval=spec.require_official_eval,
            )
            result = run_single.run()
            run_result_type = type(result).__name__
        except Exception as exc:  # pragma: no cover - live failure path is environment-specific
            run_error = f"{type(exc).__name__}: {exc}"
        finally:
            if attachment is not None and run_single is not None:
                attachment.restore(run_single)

    patch_archive = (
        _archive_native_patch_artifacts(config_path=spec.config_path, output_dir=spec.output_dir)
        if should_run and run_error is None
        else {
            **_native_output_metadata(spec.config_path),
            "patch_archived": False,
            "patch_archive_path": (spec.output_dir / "sweagent_live_single.patch").as_posix(),
            "patch_archive_sha256": None,
            "patch_source_kind": "not_run",
            "patch_source_path": None,
            "prediction_archived": False,
            "prediction_archive_path": (spec.output_dir / "sweagent_live_single.pred").as_posix(),
            "prediction_archive_sha256": None,
            "trajectory_archived": False,
            "trajectory_archive_path": (spec.output_dir / "sweagent_live_single.traj").as_posix(),
            "trajectory_archive_sha256": None,
            "artifacts": [],
        }
    )

    gates.update(
        {
            "no_unrequested_run": spec.execute or not should_run,
            "run_completed_if_execute": not spec.execute or (should_run and run_error is None),
            "capsule_materialized_if_execute": not spec.execute
            or bool(attachment and attachment.hook.capsule is not None),
            "patch_archive_attempted_if_execute": not spec.execute or bool(patch_archive),
            "raw_probe_payload_not_logged": True,
        }
    )
    if not spec.execute:
        decision = "sweagent_live_single_planned_no_run"
    elif not authorized:
        decision = "sweagent_live_single_blocked_needs_ack"
    elif not gates["config_path_exists"]:
        decision = "sweagent_live_single_blocked_missing_config"
    elif not treatment_reference_ready:
        decision = "sweagent_live_single_blocked_missing_reference_capsule"
    elif not replay_ready:
        decision = "sweagent_live_single_blocked_missing_replay_actions"
    elif run_error is not None:
        decision = "sweagent_live_single_run_failed"
    else:
        decision = "sweagent_live_single_run_completed"

    protocol_path = spec.output_dir / "sweagent_live_single_protocol.json"
    replay_path = spec.output_dir / "sweagent_live_single_replay_actions.json"
    features_path = spec.output_dir / "sweagent_live_single_features.json"
    report_path = spec.output_dir / "sweagent_live_single_report.json"
    manifest_path = spec.output_dir / "sweagent_live_single_manifest.json"
    events_path = spec.output_dir / "sweagent_live_single_events.jsonl"
    capsule_path = spec.output_dir / "sweagent_live_single_capsule.json"

    _write_json(protocol_path, spec.protocol.to_dict())
    _write_json(replay_path, spec.replay_actions)
    _write_json(features_path, spec.features)
    events = []
    if attachment is not None:
        events = [
            {"event_type": "model_query", **event} for event in attachment.hybrid_model.event_rows()
        ] + [{"event_type": "capsule_hook", **event} for event in attachment.hook.safe_audit_events]
        if attachment.hook.capsule is not None:
            _write_json(capsule_path, attachment.hook.capsule.to_dict())
    write_jsonl(events_path, events)

    report = generate_report(
        phase=SWEAGENT_LIVE_SINGLE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_LIVE_SINGLE_VERSION,
            "claim_boundary": SWEAGENT_LIVE_SINGLE_BOUNDARY,
            "live_plan_claim_boundary": SWEAGENT_LIVE_CLAIM_BOUNDARY,
            "arm_type": spec.arm_type,
            "config_path": spec.config_path.as_posix(),
            "execute_requested": spec.execute,
            "run_single_started": should_run,
            "protocol_hash": spec.protocol.protocol_hash,
            "replay_action_count": len(spec.replay_actions),
            "reference_capsule_fingerprint": spec.reference_capsule.fingerprint
            if spec.reference_capsule
            else None,
            "capsule_fingerprint": attachment.hook.capsule.fingerprint
            if attachment and attachment.hook.capsule
            else None,
            "injection_count": attachment.hook.injection_count if attachment else 0,
            "run_error": run_error,
            "run_result_type": run_result_type,
            "source_task_id": patch_archive["source_task_id"],
            "native_output_dir": patch_archive["native_output_dir"],
            "native_task_dir": patch_archive["native_task_dir"],
            "patch_archived": patch_archive["patch_archived"],
            "patch_archive_path": patch_archive["patch_archive_path"],
            "patch_archive_sha256": patch_archive["patch_archive_sha256"],
            "patch_source_kind": patch_archive["patch_source_kind"],
            "patch_source_path": patch_archive["patch_source_path"],
            "prediction_archived": patch_archive["prediction_archived"],
            "prediction_archive_path": patch_archive["prediction_archive_path"],
            "prediction_archive_sha256": patch_archive["prediction_archive_sha256"],
            "trajectory_archived": patch_archive["trajectory_archived"],
            "trajectory_archive_path": patch_archive["trajectory_archive_path"],
            "trajectory_archive_sha256": patch_archive["trajectory_archive_sha256"],
        },
    )
    _write_json(report_path, report)
    artifacts = [
        _artifact(path)
        for path in [protocol_path, replay_path, features_path, events_path, report_path]
    ]
    if capsule_path.exists():
        artifacts.append(_artifact(capsule_path))
    artifacts.extend(artifact for artifact in patch_archive["artifacts"] if artifact["exists"])
    manifest = generate_manifest(
        phase=SWEAGENT_LIVE_SINGLE_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_LIVE_SINGLE_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "protocol_path": protocol_path,
        "replay_path": replay_path,
        "features_path": features_path,
        "events_path": events_path,
        "capsule_path": capsule_path if capsule_path.exists() else None,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "SWEAGENT_LIVE_SINGLE_PHASE",
    "SWEAGENT_LIVE_SINGLE_VERSION",
    "SWEAgentLiveSingleSpec",
    "load_features",
    "load_replay_actions",
    "run_sweagent_live_single",
]
