from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V2_PAIR_INPUTS_PHASE = "6.protocol_v2_pair_inputs"
PROTOCOL_V2_PAIR_INPUTS_VERSION = "phase6_protocol_v2_pair_inputs_v1"
MODEL_NAME = "openai/gpt-5.5"
SUPPORTED_INTERVENTION_POLICIES = {
    "break_recurrence_and_replan",
    "error_observation_recovery",
    "insert_validation_checkpoint",
    "same_action_escape",
}
REPLAY_RISK_PATTERNS = {
    "git_stash": re.compile(r"\bgit\s+stash\b"),
    "pytest": re.compile(r"\b(?:pytest|py\.test)\b"),
    "network_or_install": re.compile(r"\b(?:curl|wget|pip\s+install|git\s+clone)\b"),
    "temp_path": re.compile(r"/tmp/|\bmktemp\b|\btempfile\b"),
    "time_or_random": re.compile(r"\b(?:date|time|random|uuid)\b"),
}
BOUNDARY = (
    "Protocol v2 pair-input materialization only. It writes replay/config material "
    "for fresh-candidate selection and planned preflight; it does not start Docker, "
    "call a provider, run official eval, or claim uplift."
)


def _load_json(path: Path) -> dict[str, Any]:
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


def _safe_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return cleaned.strip("._") or "unknown"


def _find_trajectory(task_id: str, trajectory_root: Path) -> Path:
    paths = sorted(trajectory_root.glob(f"**/{task_id}/{task_id}.traj"))
    if len(paths) != 1:
        raise ValueError(f"expected exactly one trajectory for {task_id}, found {len(paths)}")
    return paths[0]


def _load_trajectory(path: Path, task_id: str) -> dict[str, Any]:
    payload = _load_json(path)
    if payload.get("environment") != task_id:
        raise ValueError(f"expected trajectory environment {task_id}")
    if not isinstance(payload.get("trajectory"), list):
        raise ValueError("trajectory field must be a list")
    if not isinstance(payload.get("replay_config"), str):
        raise ValueError("replay_config field must be a JSON string")
    return payload


def _run_single_config(trajectory: dict[str, Any], native_output_dir: Path) -> dict[str, Any]:
    config = json.loads(trajectory["replay_config"])
    config["output_dir"] = native_output_dir.resolve().as_posix()
    config["env_var_path"] = None
    config["env"]["deployment"]["python_standalone_dir"] = ""
    model = config["agent"]["model"]
    model["name"] = MODEL_NAME
    model["api_key"] = None
    model["api_base"] = None
    model["api_version"] = None
    model["temperature"] = 0.0
    model["top_p"] = None
    model["per_instance_call_limit"] = 0
    model["per_instance_cost_limit"] = 0.0
    model["total_cost_limit"] = 0.0
    return config


def _consume_flag_values(tokens: list[str], start: int) -> tuple[list[str], int]:
    values = []
    cursor = start
    while cursor < len(tokens) and not tokens[cursor].startswith("--"):
        values.append(tokens[cursor])
        cursor += 1
    return values, cursor


def _encode_editor_action(action: str, *, thought: str, index: int) -> dict[str, Any]:
    tokens = shlex.split(action)
    if len(tokens) < 3:
        raise ValueError("str_replace_editor replay action requires command and path")
    arguments: dict[str, Any] = {"command": tokens[1], "path": tokens[2]}
    cursor = 3
    while cursor < len(tokens):
        token = tokens[cursor]
        if token in {"--file_text", "--old_str", "--new_str"}:
            values, cursor = _consume_flag_values(tokens, cursor + 1)
            if len(values) != 1:
                raise ValueError(f"{token} expects exactly one value")
            arguments[token.removeprefix("--")] = values[0]
        elif token == "--insert_line":
            values, cursor = _consume_flag_values(tokens, cursor + 1)
            if len(values) != 1:
                raise ValueError("--insert_line expects exactly one value")
            arguments["insert_line"] = int(values[0])
        elif token == "--view_range":
            values, cursor = _consume_flag_values(tokens, cursor + 1)
            if len(values) == 0:
                continue
            if len(values) != 2:
                raise ValueError("--view_range expects zero or two values")
            arguments["view_range"] = [int(values[0]), int(values[1])]
        else:
            raise ValueError(f"unexpected str_replace_editor token: {token}")
    return {
        "message": thought or f"Replaying editor action {index}.",
        "tool_calls": [
            {
                "type": "function",
                "id": f"call_replay_{index}",
                "function": {
                    "name": "str_replace_editor",
                    "arguments": json.dumps(arguments, sort_keys=True),
                },
            }
        ],
    }


def _encode_function_call(action: str, *, thought: str, index: int) -> dict[str, Any]:
    tokens = shlex.split(action)
    if not tokens:
        raise ValueError("replay action cannot be empty")
    if tokens[0] == "str_replace_editor":
        return _encode_editor_action(action, thought=thought, index=index)
    return {
        "message": thought or f"Replaying shell command {index}.",
        "tool_calls": [
            {
                "type": "function",
                "id": f"call_replay_{index}",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": action}, sort_keys=True),
                },
            }
        ],
    }


def _replay_model_outputs(trajectory: dict[str, Any], prefix_count: int) -> list[dict[str, Any]]:
    outputs = []
    for index, row in enumerate(trajectory["trajectory"][:prefix_count]):
        action = row.get("action")
        if not isinstance(action, str):
            raise ValueError("trajectory action must be a string")
        thought = row.get("thought") if isinstance(row.get("thought"), str) else ""
        outputs.append(_encode_function_call(action, thought=thought, index=index))
    if len(outputs) != prefix_count:
        raise ValueError("trajectory shorter than requested prefix")
    return outputs


def _raw_replay_actions(trajectory: dict[str, Any], prefix_count: int) -> list[str]:
    actions = []
    for row in trajectory["trajectory"][:prefix_count]:
        action = row.get("action")
        if not isinstance(action, str):
            raise ValueError("trajectory action must be a string")
        actions.append(action)
    if len(actions) != prefix_count:
        raise ValueError("trajectory shorter than requested prefix")
    return actions


def _action_summary(actions: list[str]) -> dict[str, Any]:
    families = [action.split(maxsplit=1)[0] if action.strip() else "empty" for action in actions]
    family_counts = {family: families.count(family) for family in sorted(set(families))}
    return {
        "count": len(actions),
        "families": sorted(set(families)),
        "family_counts": family_counts,
        "actions_embedded_in_report": False,
    }


def _replay_determinism_screen(actions: list[str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    first_indices: dict[str, int] = {}
    for index, action in enumerate(actions, start=1):
        if not action.strip():
            counts["empty_action"] = counts.get("empty_action", 0) + 1
            first_indices.setdefault("empty_action", index)
        for name, pattern in REPLAY_RISK_PATTERNS.items():
            if pattern.search(action):
                counts[name] = counts.get(name, 0) + 1
                first_indices.setdefault(name, index)
    high_risk = counts.get("git_stash", 0) > 0 or counts.get("empty_action", 0) > 0
    medium_risk = counts.get("pytest", 0) >= 5 or counts.get("network_or_install", 0) > 0
    if high_risk:
        risk_level = "high_replay_nondeterminism_risk"
    elif medium_risk:
        risk_level = "medium_replay_nondeterminism_risk"
    elif counts:
        risk_level = "low_replay_nondeterminism_risk"
    else:
        risk_level = "no_known_replay_nondeterminism_patterns"
    return {
        "risk_level": risk_level,
        "risk_counts": dict(sorted(counts.items())),
        "first_occurrence_indices": dict(sorted(first_indices.items())),
        "known_message_prefix_risk": high_risk,
        "raw_actions_embedded_in_report": False,
    }


def _safe_candidate(row: dict[str, Any]) -> dict[str, Any]:
    candidate = {
        key: deepcopy(row.get(key))
        for key in [
            "candidate_diagnostic_score",
            "candidate_prefix_sha256",
            "candidate_reason_codes",
            "intervention_policy_id",
            "pair_id",
            "selection_role",
            "source_family",
            "source_task_id",
        ]
        if key in row
    }
    candidate["candidate_static_prefix_index"] = row.get(
        "candidate_static_prefix_index",
        row.get("candidate_prefix_index"),
    )
    candidate["recalibrated_trigger_mode"] = "live_feature_signature_window"
    candidate["exact_static_prefix_trigger_disabled"] = True
    candidate["selection_status"] = "eligible_for_live_pair"
    candidate["same_pair_posthoc_positive_claim_allowed"] = False
    candidate["phase6_live_pair_authorized"] = False
    candidate["official_eval_authorized"] = False
    return candidate


def _input_report(
    *,
    candidate: dict[str, Any],
    trajectory_path: Path,
    output_dir: Path,
    native_output_dir: Path,
    actions: list[str],
    outputs: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    policy_id = str(candidate.get("intervention_policy_id") or "")
    prefix_count = int(candidate["candidate_static_prefix_index"])
    replay_screen = _replay_determinism_screen(actions)
    audit_payload = {
        "candidate": candidate,
        "replay_action_summary": _action_summary(actions),
        "replay_determinism_screen": replay_screen,
    }
    gates = {
        "trajectory_exists": trajectory_path.is_file(),
        "candidate_is_failure_target": candidate.get("selection_role") == "failure_target",
        "candidate_uses_supported_intervention_policy": policy_id
        in SUPPORTED_INTERVENTION_POLICIES,
        "candidate_uses_live_feature_mode": candidate.get("recalibrated_trigger_mode")
        == "live_feature_signature_window",
        "static_prefix_disabled": candidate.get("exact_static_prefix_trigger_disabled") is True,
        "prefix_count_matches_candidate": len(outputs) == prefix_count,
        "run_single_config_written": (output_dir / f"{candidate['source_task_id']}_run_single_config.json").is_file(),
        "replay_actions_written": (output_dir / f"{candidate['source_task_id']}_replay_actions.json").is_file(),
        "provider_secret_not_written": no_secret_literal(config) and no_secret_literal(outputs),
        "report_does_not_embed_raw_actions": True,
        "runner_not_started": True,
        "model_call_not_started": True,
        "docker_or_official_eval_not_started": True,
    }
    return generate_report(
        phase=PROTOCOL_V2_PAIR_INPUTS_PHASE,
        decision=(
            "protocol_v2_pair_inputs_ready" if all(gates.values()) else "protocol_v2_pair_inputs_blocked"
        ),
        gate_results=gates,
        extras={
            "version": PROTOCOL_V2_PAIR_INPUTS_VERSION,
            "claim_boundary": BOUNDARY,
            "pair_id": candidate.get("pair_id"),
            "source_task_id": candidate.get("source_task_id"),
            "model_name": MODEL_NAME,
            "candidate_prefix_index": prefix_count,
            "trajectory_action_count": len(actions),
            "replay_action_summary": _action_summary(actions),
            "replay_determinism_screen": replay_screen,
            "inputs": {
                "legacy_trajectory_path": trajectory_path.as_posix(),
                "native_output_dir": native_output_dir.as_posix(),
            },
            "candidate_payload_has_no_raw_payload_keys": no_raw_payload(audit_payload),
            "candidate_payload_has_no_secret_literals": no_secret_literal(audit_payload),
        },
    )


def materialize_protocol_v2_pair_input(
    *,
    candidate_row: dict[str, Any],
    trajectory_root: Path,
    output_root: Path,
    native_root: Path,
) -> dict[str, Any]:
    candidate = _safe_candidate(candidate_row)
    task_id = str(candidate.get("source_task_id") or "")
    if not task_id:
        raise ValueError("candidate source_task_id is required")
    slug = _safe_component(task_id)
    output_dir = output_root / slug
    native_output_dir = native_root / slug
    output_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = _find_trajectory(task_id, trajectory_root)
    trajectory = _load_trajectory(trajectory_path, task_id)
    prefix_count = int(candidate["candidate_static_prefix_index"])
    actions = _raw_replay_actions(trajectory, prefix_count)
    outputs = _replay_model_outputs(trajectory, prefix_count)
    config = _run_single_config(trajectory, native_output_dir)

    config_path = output_dir / f"{slug}_run_single_config.json"
    replay_path = output_dir / f"{slug}_replay_actions.json"
    candidate_path = output_dir / f"{slug}_candidate.jsonl"
    report_path = output_dir / f"{slug}_live_pair_inputs_report.json"
    manifest_path = output_dir / f"{slug}_live_pair_inputs_manifest.json"

    _write_json(config_path, config)
    _write_json(replay_path, outputs)
    write_jsonl(candidate_path, [candidate])
    report = _input_report(
        candidate=candidate,
        trajectory_path=trajectory_path,
        output_dir=output_dir,
        native_output_dir=native_output_dir,
        actions=actions,
        outputs=outputs,
        config=config,
    )
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=PROTOCOL_V2_PAIR_INPUTS_PHASE,
        report=report,
        artifacts=[
            _artifact(trajectory_path),
            _artifact(config_path),
            _artifact(replay_path),
            _artifact(candidate_path),
            _artifact(report_path),
        ],
    )
    manifest["version"] = PROTOCOL_V2_PAIR_INPUTS_VERSION
    _write_json(manifest_path, manifest)
    return {
        "candidate": candidate,
        "report": report,
        "manifest": manifest,
        "config_path": config_path,
        "replay_path": replay_path,
        "candidate_path": candidate_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


def write_protocol_v2_pair_inputs_evidence(
    *,
    candidate_rows: list[dict[str, Any]],
    trajectory_root: Path,
    output_root: Path,
    native_root: Path,
    pair_ids: list[str] | None = None,
) -> dict[str, Any]:
    wanted = set(pair_ids or [])
    selected = [
        row
        for row in candidate_rows
        if not wanted or str(row.get("pair_id") or "") in wanted
    ]
    results = []
    failures = []
    for row in selected:
        try:
            results.append(
                materialize_protocol_v2_pair_input(
                    candidate_row=row,
                    trajectory_root=trajectory_root,
                    output_root=output_root,
                    native_root=native_root,
                )
            )
        except Exception as exc:  # noqa: BLE001 - evidence package records blocked rows.
            failures.append(
                {
                    "pair_id": row.get("pair_id"),
                    "source_task_id": row.get("source_task_id"),
                    "error": str(exc),
                }
            )
    summary = {
        "candidate_row_count": len(selected),
        "materialized_count": len(results),
        "failed_count": len(failures),
        "ready_count": sum(1 for result in results if result["report"].get("passed") is True),
        "low_or_no_replay_risk_count": sum(
            1
            for result in results
            if result["report"].get("replay_determinism_screen", {}).get("risk_level")
            in {"no_known_replay_nondeterminism_patterns", "low_replay_nondeterminism_risk"}
        ),
    }
    report = generate_report(
        phase=PROTOCOL_V2_PAIR_INPUTS_PHASE,
        decision=(
            "protocol_v2_pair_inputs_batch_ready"
            if summary["materialized_count"] > 0
            else "protocol_v2_pair_inputs_batch_blocked"
        ),
        gate_results={
            "candidate_rows_present": len(selected) > 0,
            "at_least_one_materialized": summary["materialized_count"] > 0,
            "runner_not_started": True,
            "model_call_not_started": True,
            "docker_or_official_eval_not_started": True,
        },
        extras={
            "version": PROTOCOL_V2_PAIR_INPUTS_VERSION,
            "claim_boundary": BOUNDARY,
            "summary": summary,
            "materialized": [
                {
                    "pair_id": result["report"].get("pair_id"),
                    "source_task_id": result["report"].get("source_task_id"),
                    "decision": result["report"].get("decision"),
                    "replay_risk_level": result["report"]
                    .get("replay_determinism_screen", {})
                    .get("risk_level"),
                    "report_path": result["report_path"].as_posix(),
                }
                for result in results
            ],
            "failures": failures,
        },
    )
    report_path = output_root / "protocol_v2_pair_inputs_batch_report.json"
    manifest_path = output_root / "protocol_v2_pair_inputs_batch_manifest.json"
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=PROTOCOL_V2_PAIR_INPUTS_PHASE,
        report=report,
        artifacts=[
            *[_artifact(result["report_path"]) for result in results],
            _artifact(report_path),
        ],
    )
    manifest["version"] = PROTOCOL_V2_PAIR_INPUTS_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "results": results,
    }


__all__ = [
    "materialize_protocol_v2_pair_input",
    "write_protocol_v2_pair_inputs_evidence",
]
