from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.intervention.protocol_v2 import ProtocolV2
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V2_PLANNED_PREFLIGHT_PHASE = "6.protocol_v2_planned_preflight"
PROTOCOL_V2_PLANNED_PREFLIGHT_VERSION = "phase6_protocol_v2_planned_preflight_v1"
EXPECTED_CANDIDATE_DECISIONS = {
    "protocol_v2_fresh_candidate_set_ready_for_planned_preflight",
    "protocol_v2_fresh_candidate_set_ready_limited_underpowered_no_batch_claim",
}
HOOK_ACTION_IDS = {
    "interrupt_repeated_failure_loop": "v2_interrupt_repeated_failure_loop",
    "require_explicit_failure_reproduction": "v2_require_explicit_failure_reproduction",
    "require_alternative_hypothesis_before_next_patch": (
        "v2_require_alternative_hypothesis_before_next_patch"
    ),
    "require_targeted_post_patch_recheck": "v2_require_targeted_post_patch_recheck",
    "interrupt_local_file_fixation": "v2_interrupt_local_file_fixation",
    "require_adjacent_symbol_or_callsite_scan": "v2_require_adjacent_symbol_or_callsite_scan",
    "require_hypothesis_update_from_new_context": "v2_require_hypothesis_update_from_new_context",
}
BOUNDARY = (
    "Protocol v2 planned preflight validates one fresh failure target, replay material, "
    "secret-free runtime configs, and prescription-to-hook action mapping. It does not "
    "start Docker, call a provider, run official eval, or claim uplift."
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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


def _safe_read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _resolve_path(raw: str | None, *, base: Path) -> Path:
    if not raw:
        return base / "__missing__"
    path = Path(raw)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, base / path, base.parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _runtime_config(
    *,
    source_config: dict[str, Any],
    arm_type: str,
    output_dir: Path,
    model_name: str | None = None,
    api_base: str | None = None,
    provider_key_env: str = "OPENAI_API_KEY",
    provider_api_base_env: str = "OPENAI_API_BASE",
) -> dict[str, Any]:
    config = deepcopy(source_config)
    agent = config.setdefault("agent", {})
    model = agent.setdefault("model", {})
    if model_name:
        model["name"] = model_name
    if api_base is not None:
        model["api_base"] = api_base
    model["api_key"] = None
    model["per_instance_call_limit"] = int(model.get("per_instance_call_limit") or 0)
    model["per_instance_cost_limit"] = float(model.get("per_instance_cost_limit") or 0.0)
    model["total_cost_limit"] = float(model.get("total_cost_limit") or 0.0)
    config["output_dir"] = (output_dir / "native" / arm_type).resolve().as_posix()
    config.setdefault("wutai_clinic", {})
    config["wutai_clinic"].update(
        {
            "protocol": "protocol_v2_prescription",
            "arm_type": arm_type,
            "provider_env_contract": {
                "api_key_env": provider_key_env,
                "api_base_env": provider_api_base_env,
                "secrets_persisted": False,
            },
        }
    )
    return config


def _mapping_rows(protocol: ProtocolV2) -> list[dict[str, Any]]:
    rows = []
    for index, step in enumerate(protocol.action.steps, start=1):
        rows.append(
            {
                "step_index": index,
                "protocol_v2_step": step,
                "hook_action_id": HOOK_ACTION_IDS.get(step),
                "mapped": step in HOOK_ACTION_IDS,
            }
        )
    return rows


def _candidate_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key
        in {
            "fresh_rank",
            "pair_id",
            "source_task_id",
            "source_family",
            "selection_role",
            "intervention_policy_id",
            "candidate_static_prefix_index",
            "replay_risk_level",
            "protocol_v2_required",
            "same_pair_posthoc_positive_claim_allowed",
            "phase6_live_pair_authorized",
            "official_eval_authorized",
        }
    }


def protocol_v2_planned_preflight_report(
    *,
    candidate_set_report: dict[str, Any],
    candidate: dict[str, Any],
    replay_actions: list[Any],
    source_config: dict[str, Any],
    protocol_v2: ProtocolV2,
) -> dict[str, Any]:
    mappings = _mapping_rows(protocol_v2)
    audit_payload = {
        "candidate": _candidate_projection(candidate),
        "protocol_hash": protocol_v2.protocol_hash,
        "mappings": mappings,
    }
    gates = {
        "candidate_set_report_passed": candidate_set_report.get("passed") is True,
        "candidate_set_decision_allows_preflight": candidate_set_report.get("decision")
        in EXPECTED_CANDIDATE_DECISIONS,
        "candidate_is_failure_target": candidate.get("selection_role") == "failure_target",
        "candidate_not_authorized_for_live_run": candidate.get("phase6_live_pair_authorized") is False,
        "candidate_not_authorized_for_official_eval": candidate.get("official_eval_authorized") is False,
        "candidate_blocks_same_pair_positive_claim": (
            candidate.get("same_pair_posthoc_positive_claim_allowed") is False
        ),
        "replay_actions_present": len(replay_actions) > 0,
        "run_single_config_present": bool(source_config),
        "protocol_v2_valid": True,
        "all_prescription_steps_mapped_to_hook_actions": all(row["mapped"] for row in mappings),
        "control_runtime_config_generatable": bool(source_config),
        "treatment_runtime_config_generatable": bool(source_config),
        "official_outcome_not_runtime_visible": no_secret_literal(audit_payload)
        and all(
            term not in json.dumps(audit_payload, sort_keys=True).lower()
            for term in (
                "official_eval_resolved",
                "control_resolved",
                "intervention_resolved",
                "effect_label",
                "fail_to_pass",
                "pass_to_pass",
                "pass_to_fail",
            )
        ),
        "preflight_payload_has_no_raw_payload_keys": no_raw_payload(audit_payload),
        "preflight_payload_has_no_secret_literals": no_secret_literal(audit_payload),
        "runner_not_started": True,
        "model_call_not_started": True,
        "docker_or_official_eval_not_started": True,
    }
    return generate_report(
        phase=PROTOCOL_V2_PLANNED_PREFLIGHT_PHASE,
        decision=(
            "protocol_v2_planned_preflight_ready_live_execution_not_authorized"
            if all(gates.values())
            else "protocol_v2_planned_preflight_blocked"
        ),
        gate_results=gates,
        extras={
            "version": PROTOCOL_V2_PLANNED_PREFLIGHT_VERSION,
            "claim_boundary": BOUNDARY,
            "pair_id": candidate.get("pair_id"),
            "source_task_id": candidate.get("source_task_id"),
            "summary": {
                "replay_action_count": len(replay_actions),
                "mapping_count": len(mappings),
                "all_steps_mapped": all(row["mapped"] for row in mappings),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "live_execution_authorized": False,
                "official_eval_authorized": False,
            },
            "hook_action_mapping": mappings,
            "continuation_policy": {
                "allow_protocol_v2_live_single_execute": all(gates.values()),
                "allow_protocol_v2_real_run_without_explicit_ack": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "execute_first_protocol_v2_control_and_treatment_arms_with_explicit_ack"
                    if all(gates.values())
                    else "fix_protocol_v2_preflight_inputs_before_live_execution"
                ),
            },
        },
    )


def write_protocol_v2_planned_preflight_evidence(
    *,
    candidate_set_report: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    protocol_v2: ProtocolV2,
    output_dir: Path,
    source_task_id: str | None = None,
    model_name: str | None = None,
    api_base: str | None = None,
    provider_key_env: str = "OPENAI_API_KEY",
    provider_api_base_env: str = "OPENAI_API_BASE",
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = {}
    for row in candidate_rows:
        if source_task_id is None or row.get("source_task_id") == source_task_id:
            candidate = row
            break
    base = output_dir.resolve()
    replay_path = _resolve_path(candidate.get("replay_actions_path"), base=base)
    config_path = _resolve_path(candidate.get("run_single_config_path"), base=base)
    replay_actions = _load_json(replay_path) if replay_path.is_file() else []
    source_config = _load_json(config_path) if config_path.is_file() else {}
    report = protocol_v2_planned_preflight_report(
        candidate_set_report=candidate_set_report,
        candidate=candidate,
        replay_actions=replay_actions if isinstance(replay_actions, list) else [],
        source_config=source_config if isinstance(source_config, dict) else {},
        protocol_v2=protocol_v2,
    )

    candidate_path = output_dir / "protocol_v2_planned_preflight_candidate.json"
    mapping_path = output_dir / "protocol_v2_planned_preflight_hook_mapping.jsonl"
    control_config_path = output_dir / "control" / "protocol_v2_runtime_config.json"
    treatment_config_path = output_dir / "treatment" / "protocol_v2_runtime_config.json"
    report_path = output_dir / "protocol_v2_planned_preflight_report.json"
    summary_path = output_dir / "protocol_v2_planned_preflight_summary.json"
    manifest_path = output_dir / "protocol_v2_planned_preflight_manifest.json"

    _write_json(candidate_path, _candidate_projection(candidate))
    write_jsonl(mapping_path, report["hook_action_mapping"])
    if isinstance(source_config, dict) and source_config:
        _write_json(
            control_config_path,
            _runtime_config(
                source_config=source_config,
                arm_type="control",
                output_dir=output_dir,
                model_name=model_name,
                api_base=api_base,
                provider_key_env=provider_key_env,
                provider_api_base_env=provider_api_base_env,
            ),
        )
        _write_json(
            treatment_config_path,
            _runtime_config(
                source_config=source_config,
                arm_type="treatment",
                output_dir=output_dir,
                model_name=model_name,
                api_base=api_base,
                provider_key_env=provider_key_env,
                provider_api_base_env=provider_api_base_env,
            ),
        )
    _write_json(report_path, report)
    _write_json(
        summary_path,
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "summary": report["summary"],
            "continuation_policy": report["continuation_policy"],
        },
    )
    artifacts = [
        _artifact(path)
        for path in [
            candidate_path,
            mapping_path,
            control_config_path,
            treatment_config_path,
            report_path,
            summary_path,
            replay_path,
            config_path,
        ]
    ]
    artifacts.extend(_artifact(path) for path in input_artifacts or [] if path.exists())
    manifest = generate_manifest(
        phase=PROTOCOL_V2_PLANNED_PREFLIGHT_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V2_PLANNED_PREFLIGHT_VERSION
    _write_json(manifest_path, manifest)
    return {
        "candidate": candidate,
        "report": report,
        "manifest": manifest,
        "candidate_path": candidate_path,
        "mapping_path": mapping_path,
        "control_config_path": control_config_path,
        "treatment_config_path": treatment_config_path,
        "report_path": report_path,
        "summary_path": summary_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "HOOK_ACTION_IDS",
    "PROTOCOL_V2_PLANNED_PREFLIGHT_VERSION",
    "protocol_v2_planned_preflight_report",
    "write_protocol_v2_planned_preflight_evidence",
]
