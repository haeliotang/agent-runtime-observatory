from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live import (
    RunSingleFactory,
    SWEAgentLiveSingleSpec,
    load_replay_actions,
    run_sweagent_live_single,
)
from wutai_clinic.adapters.sweagent_live_pair import SWEAgentLivePairSpec, run_sweagent_live_pair
from wutai_clinic.intervention.hooks import INTERVENTION_POLICIES
from wutai_clinic.intervention.replay_protocol import InterventionProtocol, StateCapsule
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_LIVE_HOOK_PREFLIGHT_PHASE = "5.sweagent_live_hook_runner_preflight"
SWEAGENT_LIVE_HOOK_PREFLIGHT_VERSION = "phase5_sweagent_live_hook_runner_preflight_v1"
READY_READINESS_DECISIONS = {
    "batch3_readiness_live_feature_dry_run_ready_external_run_not_authorized",
    "phase63_low_nondeterminism_live_candidate_set_ready_for_offline_preflight",
}
BOUNDARY = (
    "This package prepares or executes one operator-authorized SWE-agent live-hook runner "
    "preflight pair. It is not a batch-3 real run, does not run full official evaluation, "
    "and does not make a generalized uplift or EFE/STR prediction claim."
)


@dataclass(frozen=True)
class SWEAgentLiveHookPreflightSpec:
    readiness_report: dict[str, Any]
    candidate_rows: list[dict[str, Any]]
    run_single_config: Path
    output_dir: Path
    pair_id: str | None = None
    replay_actions_path: Path | None = None
    execute: bool = False
    require_official_eval: bool = False


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


def _select_candidate(
    candidate_rows: list[dict[str, Any]], pair_id: str | None
) -> dict[str, Any] | None:
    if not candidate_rows:
        return None
    if pair_id is None:
        return candidate_rows[0]
    for row in candidate_rows:
        if row.get("pair_id") == pair_id:
            return row
    return None


def _candidate_features(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    context = candidate.get("candidate_prefix_only_context", {})
    reason_codes = set(candidate.get("candidate_reason_codes", []))
    return {
        "error_streak": 1 if "error_streak_or_error_observation" in reason_codes else 0,
        "validation_gap_after_edit": int(context.get("validation_gap_steps", 0) or 0),
        "same_action_family_streak": int(context.get("same_action_family_streak", 0) or 0),
        "duplicate_state_ratio": 0.75
        if {"recurrence_spike", "loop_or_duplicate_pattern"} & reason_codes
        else 0.0,
        "step_count": int(context.get("step_count", 0) or 0),
        "candidate_static_prefix_index": int(
            candidate.get("candidate_static_prefix_index", 0) or 0
        ),
    }


def _candidate_protocol(candidate: dict[str, Any] | None) -> InterventionProtocol:
    policy_id = str(
        (candidate or {}).get("intervention_policy_id") or "break_recurrence_and_replan"
    )
    if policy_id not in INTERVENTION_POLICIES:
        policy_id = "break_recurrence_and_replan"
    if policy_id == "insert_validation_checkpoint":
        predicate = "validation_gap_after_edit >= 1"
    elif policy_id == "error_observation_recovery":
        predicate = "error_streak >= 1"
    elif policy_id == "same_action_escape":
        predicate = "same_action_family_streak >= 2"
    else:
        predicate = "duplicate_state_ratio >= 0.5"
    return InterventionProtocol.from_dict(
        {
            "trigger": {"type": "live_feature", "predicate": predicate},
            "action": {"type": "inject_system_prompt", "message_id": policy_id},
            "guard": {"debounce": "once_per_pair", "raw_payload_logging": False},
            "claim": {"allowed": "bounded_next_step_control"},
        }
    )


def _planned_command(
    *,
    config: Path,
    output_dir: Path,
    arm: str,
    protocol: Path,
    replay_actions: Path | None,
    features: Path,
    reference_capsule: Path | None = None,
) -> str:
    replay_arg = (
        shlex.quote(replay_actions.as_posix())
        if replay_actions is not None
        else "<REAL_REPLAY_ACTIONS_JSON>"
    )
    parts = [
        "wutai-clinic",
        "sweagent-live-single",
        shlex.quote(config.as_posix()),
        "-o",
        shlex.quote(output_dir.as_posix()),
        "--arm",
        arm,
        "--protocol",
        shlex.quote(protocol.as_posix()),
        "--replay-actions",
        replay_arg,
        "--features",
        shlex.quote(features.as_posix()),
        "--execute",
        "--ack-docker",
        "--ack-external-provider",
    ]
    if reference_capsule is not None:
        parts.extend(["--reference-capsule", shlex.quote(reference_capsule.as_posix())])
    return " ".join(parts)


def _load_replay(path: Path | None) -> list[dict[str, Any] | str]:
    return load_replay_actions(path) if path is not None else []


def _live_hook_gates(
    *,
    spec: SWEAgentLiveHookPreflightSpec,
    candidate: dict[str, Any] | None,
    replay_actions: list[dict[str, Any] | str],
    policy: RuntimePermissionPolicy,
) -> dict[str, bool]:
    readiness_policy = spec.readiness_report.get("continuation_policy", {})
    return {
        "readiness_report_passed": spec.readiness_report.get("passed") is True,
        "readiness_decision_allows_live_hook_preflight": (
            spec.readiness_report.get("decision") in READY_READINESS_DECISIONS
        ),
        "readiness_allows_live_hook_runner_preflight": (
            readiness_policy.get("allow_live_hook_runner_preflight") is True
        ),
        "readiness_still_blocks_batch3_real_run": (
            readiness_policy.get("allow_batch3_real_run") is False
        ),
        "candidate_row_selected": candidate is not None,
        "candidate_uses_live_feature_mode": (
            candidate is not None
            and candidate.get("recalibrated_trigger_mode") == "live_feature_signature_window"
        ),
        "candidate_disables_exact_static_prefix": (
            candidate is not None and candidate.get("exact_static_prefix_trigger_disabled") is True
        ),
        "candidate_not_batch3_authorized": (
            candidate is not None and candidate.get("batch3_real_run_authorized") is False
        ),
        "run_single_config_exists": spec.run_single_config.is_file(),
        "replay_actions_present_if_execute": not spec.execute or len(replay_actions) > 0,
        "docker_ack_if_execute": not spec.execute or policy.allow_docker,
        "external_provider_ack_if_execute": not spec.execute or policy.allow_external_provider,
        "official_eval_ack_if_required": (
            not spec.require_official_eval or policy.allow_official_eval
        ),
        "full_batch_not_requested": True,
        "official_eval_not_claimed_by_preflight": True,
        "generalized_claim_not_made": True,
    }


def _decision(gates: dict[str, bool], *, execute: bool, pair_report: dict[str, Any] | None) -> str:
    if not all(gates.values()):
        return "sweagent_live_hook_preflight_blocked"
    if not execute:
        return "sweagent_live_hook_preflight_planned_no_run"
    if pair_report is None or pair_report.get("passed") is not True:
        return "sweagent_live_hook_preflight_run_incomplete"
    return "sweagent_live_hook_preflight_pair_ready_pending_official_eval"


def run_sweagent_live_hook_preflight(
    *,
    spec: SWEAgentLiveHookPreflightSpec,
    policy: RuntimePermissionPolicy,
    run_single_factory: RunSingleFactory | None = None,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    candidate = _select_candidate(spec.candidate_rows, spec.pair_id)
    features = _candidate_features(candidate)
    protocol = _candidate_protocol(candidate)
    replay_actions = _load_replay(spec.replay_actions_path)
    gates = _live_hook_gates(
        spec=spec,
        candidate=candidate,
        replay_actions=replay_actions,
        policy=policy,
    )

    protocol_path = spec.output_dir / "live_hook_preflight_protocol.json"
    features_path = spec.output_dir / "live_hook_preflight_features.json"
    replay_path = spec.output_dir / "live_hook_preflight_replay_actions.json"
    selected_candidate_path = spec.output_dir / "live_hook_preflight_candidate.jsonl"
    commands_path = spec.output_dir / "live_hook_preflight_commands.json"
    report_path = spec.output_dir / "live_hook_preflight_report.json"
    manifest_path = spec.output_dir / "live_hook_preflight_manifest.json"
    control_dir = spec.output_dir / "control"
    treatment_dir = spec.output_dir / "treatment"
    pair_dir = spec.output_dir / "pair"

    _write_json(protocol_path, protocol.to_dict())
    _write_json(features_path, features)
    _write_json(replay_path, replay_actions)
    write_jsonl(selected_candidate_path, [candidate] if candidate else [])
    commands = {
        "requires_real_replay_actions_before_execute": spec.replay_actions_path is None,
        "control": _planned_command(
            config=spec.run_single_config,
            output_dir=control_dir,
            arm="control",
            protocol=protocol_path,
            replay_actions=replay_path if spec.replay_actions_path is not None else None,
            features=features_path,
        ),
        "treatment_after_control_capsule": _planned_command(
            config=spec.run_single_config,
            output_dir=treatment_dir,
            arm="treatment",
            protocol=protocol_path,
            replay_actions=replay_path if spec.replay_actions_path is not None else None,
            features=features_path,
            reference_capsule=control_dir / "sweagent_live_single_capsule.json",
        ),
    }
    _write_json(commands_path, commands)

    control_result = None
    treatment_result = None
    pair_result = None
    if all(gates.values()) and spec.execute:
        control_result = run_sweagent_live_single(
            spec=SWEAgentLiveSingleSpec(
                config_path=spec.run_single_config,
                output_dir=control_dir,
                arm_type="control",
                execute=True,
                protocol=protocol,
                replay_actions=replay_actions,
                features=features,
                require_official_eval=spec.require_official_eval,
            ),
            policy=policy,
            run_single_factory=run_single_factory,
        )
        control_capsule_path = control_result["capsule_path"]
        if (
            control_result["report"].get("passed") is True
            and control_capsule_path is not None
            and Path(control_capsule_path).is_file()
        ):
            treatment_result = run_sweagent_live_single(
                spec=SWEAgentLiveSingleSpec(
                    config_path=spec.run_single_config,
                    output_dir=treatment_dir,
                    arm_type="treatment",
                    execute=True,
                    protocol=protocol,
                    replay_actions=replay_actions,
                    features=features,
                    reference_capsule=StateCapsule.from_file(Path(control_capsule_path)),
                    require_official_eval=spec.require_official_eval,
                ),
                policy=policy,
                run_single_factory=run_single_factory,
            )
        if treatment_result is not None and treatment_result["report"].get("passed") is True:
            pair_result = run_sweagent_live_pair(
                spec=SWEAgentLivePairSpec(
                    control_dir=control_dir,
                    treatment_dir=treatment_dir,
                    output_dir=pair_dir,
                    outcome_source="not_provided",
                ),
                policy=RuntimePermissionPolicy(),
            )

    pair_report = pair_result["report"] if pair_result is not None else None
    execution_summary = {
        "execute_requested": spec.execute,
        "control_started": bool(
            control_result and control_result["report"].get("run_single_started") is True
        ),
        "treatment_started": bool(
            treatment_result and treatment_result["report"].get("run_single_started") is True
        ),
        "control_decision": control_result["report"].get("decision") if control_result else None,
        "treatment_decision": (
            treatment_result["report"].get("decision") if treatment_result else None
        ),
        "pair_decision": pair_report.get("decision") if pair_report else None,
        "pair_effect_label": pair_report.get("effect_label") if pair_report else None,
        "official_eval_outcome_present": False,
    }
    report = generate_report(
        phase=SWEAGENT_LIVE_HOOK_PREFLIGHT_PHASE,
        decision=_decision(gates, execute=spec.execute, pair_report=pair_report),
        gate_results=gates,
        extras={
            "version": SWEAGENT_LIVE_HOOK_PREFLIGHT_VERSION,
            "claim_boundary": BOUNDARY,
            "pair_id": candidate.get("pair_id") if candidate else spec.pair_id,
            "source_task_id": candidate.get("source_task_id") if candidate else None,
            "candidate_policy_id": candidate.get("intervention_policy_id") if candidate else None,
            "protocol_hash": protocol.protocol_hash,
            "feature_keys": sorted(features),
            "replay_action_count": len(replay_actions),
            "commands": commands,
            "execution_summary": execution_summary,
            "continuation_policy": {
                "allow_single_pair_live_hook_preflight": all(gates.values()),
                "allow_batch3_real_run": False,
                "allow_full_batch": False,
                "allow_official_uplift_claim": False,
                "recommended_next_step": "inspect_live_pair_capsules_then_run_official_eval_if_operator_authorized"
                if pair_report is not None and pair_report.get("passed") is True
                else "run_single_pair_live_hook_preflight_with_real_replay_actions_and_explicit_acks",
            },
        },
    )
    _write_json(report_path, report)
    artifacts = [
        _artifact(path)
        for path in [
            protocol_path,
            features_path,
            replay_path,
            selected_candidate_path,
            commands_path,
            report_path,
        ]
    ]
    if pair_result is not None:
        artifacts.extend(
            _artifact(path)
            for path in [
                control_result["report_path"],
                control_result["manifest_path"],
                treatment_result["report_path"],
                treatment_result["manifest_path"],
                pair_result["report_path"],
                pair_result["manifest_path"],
                pair_result["summary_path"],
            ]
        )
    manifest = generate_manifest(
        phase=SWEAGENT_LIVE_HOOK_PREFLIGHT_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_LIVE_HOOK_PREFLIGHT_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "protocol_path": protocol_path,
        "features_path": features_path,
        "replay_path": replay_path,
        "candidate_path": selected_candidate_path,
        "commands_path": commands_path,
        "control_result": control_result,
        "treatment_result": treatment_result,
        "pair_result": pair_result,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "SWEAGENT_LIVE_HOOK_PREFLIGHT_PHASE",
    "SWEAGENT_LIVE_HOOK_PREFLIGHT_VERSION",
    "SWEAgentLiveHookPreflightSpec",
    "run_sweagent_live_hook_preflight",
]
