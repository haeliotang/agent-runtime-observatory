from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.protocol_v1 import ProtocolV1
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PROTOCOL_V1_PREFLIGHT_PHASE = "6.protocol_v1_sweagent_live_hook_adapter_preflight"
SWEAGENT_PROTOCOL_V1_PREFLIGHT_VERSION = "phase6_protocol_v1_sweagent_adapter_preflight_v1"
EXPECTED_DRY_RUN_DECISION = "protocol_v1_dry_run_gate_passed_live_execution_not_authorized"
EXPECTED_PLAN_DECISION = "protocol_v1_plan_ready_not_live_executed"
BOUNDARY = (
    "This package validates that a SWE-agent live-hook adapter can interpret Protocol v1 "
    "prescriptions into auditable enforcement points. It does not start Docker, call a "
    "provider, run official eval, inject posthoc official test identifiers, or authorize "
    "same-pair positive attribution."
)

PRESCRIPTION_ENFORCEMENT = {
    "targeted_failure_oracle": {
        "materialize_prefix_observed_failure": {
            "phase": "pre_edit",
            "adapter_event": "would_require_prefix_observed_failure_materialization",
            "blocking": False,
        },
        "block_edit_until_failure_reproduced_or_explained": {
            "phase": "pre_edit",
            "adapter_event": "would_block_edit_until_failure_reproduced_or_explained",
            "blocking": True,
        },
        "require_post_patch_target_recheck": {
            "phase": "post_patch",
            "adapter_event": "would_require_post_patch_target_recheck",
            "blocking": False,
        },
    },
    "regression_guarded_patch_validation": {
        "require_post_patch_target_recheck": {
            "phase": "post_patch",
            "adapter_event": "would_require_post_patch_target_recheck",
            "blocking": False,
        },
        "require_post_patch_guard_recheck": {
            "phase": "post_patch",
            "adapter_event": "would_require_post_patch_guard_recheck",
            "blocking": False,
        },
        "block_submit_on_guard_regression": {
            "phase": "pre_submit",
            "adapter_event": "would_block_submit_on_guard_regression",
            "blocking": True,
        },
    },
}


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


def _planned_command(
    *,
    config: Path,
    output_dir: Path,
    pair_id: str,
    protocol_path: Path,
    source_task_id: str | None,
) -> str:
    return " ".join(
        [
            "wutai-clinic",
            "sweagent-protocol-v1-live-single",
            shlex.quote(config.as_posix()),
            "-o",
            shlex.quote((output_dir / pair_id).as_posix()),
            "--protocol",
            shlex.quote(protocol_path.as_posix()),
            "--pair-id",
            shlex.quote(pair_id),
            "--execute",
            "--ack-docker",
            "--ack-external-provider",
        ]
        + (["--source-task-id", shlex.quote(source_task_id)] if source_task_id is not None else [])
    )


def _safe_pair_id(value: Any, index: int) -> str:
    raw = str(value or f"pair-{index}")
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return cleaned.strip("._") or f"pair-{index}"


def _constraint_events(
    *,
    row: dict[str, Any],
    row_index: int,
    protocol: ProtocolV1,
) -> list[dict[str, Any]]:
    prescription = protocol.action.prescription_id
    enforcement = PRESCRIPTION_ENFORCEMENT[prescription]
    analysis_only = row.get("official_eval_tests_analysis_only") or {}
    events = []
    for constraint_id in protocol.action.constraint_ids:
        mapping = enforcement[constraint_id]
        events.append(
            {
                "event": mapping["adapter_event"],
                "row_index": row_index,
                "source_task_id": row.get("source_task_id"),
                "pair_id": row.get("pair_id"),
                "protocol_hash": protocol.protocol_hash,
                "prescription_id": prescription,
                "constraint_id": constraint_id,
                "enforcement_phase": mapping["phase"],
                "would_block": bool(mapping["blocking"]),
                "runtime_oracle_source": row.get("runtime_oracle_source"),
                "official_eval_identifiers_runtime_visible": (
                    protocol.guard.official_eval_identifiers_runtime_visible
                ),
                "official_eval_tests_analysis_only_counts": {
                    "target_failures": len(analysis_only.get("target_failures") or []),
                    "target_successes": len(analysis_only.get("target_successes") or []),
                    "guard_failures": len(analysis_only.get("guard_failures") or []),
                },
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "raw_payload_logged": False,
            }
        )
    return events


def sweagent_protocol_v1_preflight_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for row_index, row in enumerate(plan.get("pairs") or []):
        try:
            protocol = ProtocolV1.from_dict(row.get("protocol_v1") or {})
        except ValueError as exc:
            events.append(
                {
                    "event": "protocol_v1_adapter_preflight_invalid_protocol",
                    "row_index": row_index,
                    "source_task_id": row.get("source_task_id"),
                    "pair_id": row.get("pair_id"),
                    "protocol_error": str(exc),
                    "runner_started": False,
                    "model_call_started": False,
                    "docker_or_official_eval_started": False,
                    "raw_payload_logged": False,
                }
            )
            continue
        events.extend(_constraint_events(row=row, row_index=row_index, protocol=protocol))
    return events


def _protocol_rows_valid(plan: dict[str, Any]) -> bool:
    for row in plan.get("pairs") or []:
        try:
            ProtocolV1.from_dict(row.get("protocol_v1") or {})
        except ValueError:
            return False
    return bool(plan.get("pairs"))


def sweagent_protocol_v1_preflight_gates(
    *,
    plan: dict[str, Any],
    dry_run_report: dict[str, Any],
    run_single_config: Path,
    events: list[dict[str, Any]],
) -> dict[str, bool]:
    dry_policy = dry_run_report.get("continuation_policy") or {}
    pairs = plan.get("pairs") or []
    return {
        "dry_run_report_passed": dry_run_report.get("passed") is True,
        "dry_run_decision_expected": dry_run_report.get("decision") == EXPECTED_DRY_RUN_DECISION,
        "dry_run_allows_adapter_preflight": (
            dry_policy.get("allow_protocol_v1_live_hook_adapter_preflight") is True
        ),
        "dry_run_blocks_real_run": dry_policy.get("allow_protocol_v1_real_run") is False,
        "plan_decision_expected": plan.get("decision") == EXPECTED_PLAN_DECISION,
        "pairs_present": len(pairs) > 0,
        "pair_count_matches": int(plan.get("pair_count") or -1) == len(pairs),
        "same_pair_positive_claim_blocked": plan.get("same_pair_positive_claim_allowed") is False,
        "same_pair_rerun_attribution_blocked": bool(pairs)
        and all(row.get("same_pair_rerun_attribution_eligible") is False for row in pairs),
        "runtime_oracle_prefix_only": bool(pairs)
        and all(row.get("runtime_oracle_source") == "prefix_observation_required" for row in pairs),
        "protocol_rows_valid": _protocol_rows_valid(plan),
        "run_single_config_exists": run_single_config.is_file(),
        "adapter_events_present": len(events) > 0,
        "adapter_events_cover_all_pairs": len({event.get("row_index") for event in events})
        == len(pairs),
        "adapter_events_no_runtime_started": bool(events)
        and all(
            event["runner_started"] is False
            and event["model_call_started"] is False
            and event["docker_or_official_eval_started"] is False
            for event in events
        ),
        "official_eval_identifiers_not_runtime_visible": bool(events)
        and all(
            event.get("official_eval_identifiers_runtime_visible") is not True for event in events
        ),
        "raw_payload_logging_disabled": bool(events)
        and all(event["raw_payload_logged"] is False for event in events),
    }


def sweagent_protocol_v1_preflight_report(
    *,
    plan: dict[str, Any],
    dry_run_report: dict[str, Any],
    run_single_config: Path,
    commands: dict[str, Any],
) -> dict[str, Any]:
    events = sweagent_protocol_v1_preflight_events(plan)
    gates = sweagent_protocol_v1_preflight_gates(
        plan=plan,
        dry_run_report=dry_run_report,
        run_single_config=run_single_config,
        events=events,
    )
    prescription_counts: dict[str, int] = {}
    blocking_event_count = 0
    for event in events:
        if event.get("prescription_id"):
            key = str(event["prescription_id"])
            prescription_counts[key] = prescription_counts.get(key, 0) + 1
        if event.get("would_block") is True:
            blocking_event_count += 1
    return generate_report(
        phase=SWEAGENT_PROTOCOL_V1_PREFLIGHT_PHASE,
        decision=(
            "protocol_v1_sweagent_adapter_preflight_ready_no_run"
            if all(gates.values())
            else "protocol_v1_sweagent_adapter_preflight_blocked"
        ),
        gate_results=gates,
        extras={
            "version": SWEAGENT_PROTOCOL_V1_PREFLIGHT_VERSION,
            "claim_boundary": BOUNDARY,
            "summary": {
                "pair_count": len(plan.get("pairs") or []),
                "adapter_event_count": len(events),
                "blocking_event_count": blocking_event_count,
                "prescription_event_counts": dict(sorted(prescription_counts.items())),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "live_execution_authorized": False,
            },
            "commands": commands,
            "continuation_policy": {
                "allow_protocol_v1_constraint_hook_implementation": all(gates.values()),
                "allow_protocol_v1_real_run": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "implement_protocol_v1_constraint_hook_and_unit_test_against_preflight_events"
                    if all(gates.values())
                    else "fix_protocol_v1_adapter_preflight_inputs_before_hook_implementation"
                ),
            },
        },
    )


def write_sweagent_protocol_v1_preflight_evidence(
    *,
    protocol_v1_plan: dict[str, Any],
    dry_run_report: dict[str, Any],
    run_single_config: Path,
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    events = sweagent_protocol_v1_preflight_events(protocol_v1_plan)
    protocol_dir = output_dir / "protocols"
    protocol_dir.mkdir(parents=True, exist_ok=True)
    protocol_paths = []
    for index, row in enumerate(protocol_v1_plan.get("pairs") or []):
        protocol_path = protocol_dir / f"{_safe_pair_id(row.get('pair_id'), index)}.json"
        _write_json(protocol_path, row.get("protocol_v1") or {})
        protocol_paths.append(protocol_path)
    commands = {
        "requires_protocol_v1_constraint_hook_before_execute": True,
        "run_single_config": run_single_config.as_posix(),
        "pairs": [
            {
                "source_task_id": row.get("source_task_id"),
                "pair_id": row.get("pair_id"),
                "planned_command": _planned_command(
                    config=run_single_config,
                    output_dir=output_dir / "planned_live_runs",
                    pair_id=_safe_pair_id(row.get("pair_id"), index),
                    protocol_path=protocol_paths[index],
                    source_task_id=(
                        str(row.get("source_task_id")) if row.get("source_task_id") else None
                    ),
                ),
            }
            for index, row in enumerate(protocol_v1_plan.get("pairs") or [])
        ],
    }
    report = sweagent_protocol_v1_preflight_report(
        plan=protocol_v1_plan,
        dry_run_report=dry_run_report,
        run_single_config=run_single_config,
        commands=commands,
    )

    report_path = output_dir / "protocol_v1_sweagent_adapter_preflight_report.json"
    manifest_path = output_dir / "protocol_v1_sweagent_adapter_preflight_manifest.json"
    events_path = output_dir / "protocol_v1_sweagent_adapter_preflight_events.jsonl"
    commands_path = output_dir / "protocol_v1_sweagent_adapter_preflight_commands.json"
    summary_path = output_dir / "protocol_v1_sweagent_adapter_preflight_summary.json"

    write_jsonl(events_path, events)
    _write_json(commands_path, commands)
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
        _artifact(path) for path in [report_path, events_path, commands_path, summary_path]
    ]
    artifacts.extend(_artifact(path) for path in protocol_paths)
    artifacts.extend(_artifact(path) for path in input_artifacts or [])
    manifest = generate_manifest(
        phase=SWEAGENT_PROTOCOL_V1_PREFLIGHT_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_PROTOCOL_V1_PREFLIGHT_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "events": events,
        "commands": commands,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "events_path": events_path,
        "commands_path": commands_path,
        "summary_path": summary_path,
    }


__all__ = [
    "SWEAGENT_PROTOCOL_V1_PREFLIGHT_PHASE",
    "SWEAGENT_PROTOCOL_V1_PREFLIGHT_VERSION",
    "write_sweagent_protocol_v1_preflight_evidence",
    "sweagent_protocol_v1_preflight_report",
]
