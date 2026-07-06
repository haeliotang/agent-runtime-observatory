from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.protocol_v1 import ProtocolV1
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V1_DRY_RUN_VERSION = "phase6_protocol_v1_dry_run_gate_v1"
EXPECTED_PLAN_DECISION = "protocol_v1_plan_ready_not_live_executed"
PASS_DECISION = "protocol_v1_dry_run_gate_passed_live_execution_not_authorized"
BLOCKED_DECISION = "protocol_v1_dry_run_gate_blocked"
CLAIM_BOUNDARY = (
    "Protocol v1 dry-run validates prescription shape, runtime-oracle boundaries, and "
    "audit events only. It does not start Docker, call a provider, run official eval, "
    "reuse posthoc official test identifiers as runtime hints, or authorize same-pair "
    "positive attribution."
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


def protocol_v1_dry_run_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for index, row in enumerate(plan.get("pairs") or []):
        protocol_payload = row.get("protocol_v1") or {}
        protocol_error = None
        try:
            protocol = ProtocolV1.from_dict(protocol_payload)
            protocol_hash = protocol.protocol_hash
            prescription_id = protocol.action.prescription_id
            constraint_ids = list(protocol.action.constraint_ids)
            runtime_visible = protocol.guard.official_eval_identifiers_runtime_visible
            same_pair_allowed = protocol.guard.same_pair_rerun_attribution_allowed
            raw_payload_logged = protocol.guard.raw_payload_logging
        except ValueError as exc:
            protocol_error = str(exc)
            action = protocol_payload.get("action") if isinstance(protocol_payload, dict) else {}
            guard = protocol_payload.get("guard") if isinstance(protocol_payload, dict) else {}
            protocol_hash = None
            prescription_id = str((action or {}).get("prescription_id") or "invalid_protocol")
            constraint_ids = list((action or {}).get("constraint_ids") or [])
            runtime_visible = bool((guard or {}).get("official_eval_identifiers_runtime_visible"))
            same_pair_allowed = bool((guard or {}).get("same_pair_rerun_attribution_allowed"))
            raw_payload_logged = bool((guard or {}).get("raw_payload_logging"))
        analysis_only = row.get("official_eval_tests_analysis_only") or {}
        events.append(
            {
                "event": "protocol_v1_prescription_dry_run",
                "row_index": index,
                "source_task_id": row.get("source_task_id"),
                "pair_id": row.get("pair_id"),
                "protocol_valid": protocol_error is None,
                "protocol_error": protocol_error,
                "protocol_hash": protocol_hash,
                "prescription_id": prescription_id,
                "constraint_ids": constraint_ids,
                "runtime_oracle_source": row.get("runtime_oracle_source"),
                "official_eval_identifiers_runtime_visible": runtime_visible,
                "same_pair_rerun_attribution_allowed": same_pair_allowed,
                "official_eval_tests_analysis_only_counts": {
                    "target_failures": len(analysis_only.get("target_failures") or []),
                    "target_successes": len(analysis_only.get("target_successes") or []),
                    "guard_failures": len(analysis_only.get("guard_failures") or []),
                },
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "raw_payload_logged": raw_payload_logged,
            }
        )
    return events


def _all_pairs(plan: dict[str, Any], predicate: Any) -> bool:
    pairs = plan.get("pairs") or []
    return bool(pairs) and all(predicate(row) for row in pairs)


def protocol_v1_dry_run_gates(
    plan: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, bool]:
    pairs = plan.get("pairs") or []
    protocol_rows_valid = True
    protocol_hashes_match = True
    for row in pairs:
        try:
            protocol = ProtocolV1.from_dict(row.get("protocol_v1") or {})
        except ValueError:
            protocol_rows_valid = False
            protocol_hashes_match = False
            continue
        protocol_hashes_match = (
            protocol_hashes_match and row.get("protocol_hash") == protocol.protocol_hash
        )
    return {
        "plan_decision_expected": plan.get("decision") == EXPECTED_PLAN_DECISION,
        "pair_count_matches": int(plan.get("pair_count") or -1) == len(pairs),
        "pairs_present": len(pairs) > 0,
        "same_pair_positive_claim_blocked": plan.get("same_pair_positive_claim_allowed") is False,
        "protocol_rows_valid": protocol_rows_valid,
        "protocol_hashes_match": protocol_hashes_match,
        "same_pair_rerun_attribution_blocked": _all_pairs(
            plan, lambda row: row.get("same_pair_rerun_attribution_eligible") is False
        ),
        "runtime_oracle_prefix_only": _all_pairs(
            plan, lambda row: row.get("runtime_oracle_source") == "prefix_observation_required"
        ),
        "official_eval_tests_analysis_only_present": _all_pairs(
            plan, lambda row: isinstance(row.get("official_eval_tests_analysis_only"), dict)
        ),
        "dry_run_event_count_matches_pairs": len(events) == len(pairs),
        "dry_run_started_no_model_or_runner": bool(events)
        and all(
            event["runner_started"] is False
            and event["model_call_started"] is False
            and event["docker_or_official_eval_started"] is False
            for event in events
        ),
        "raw_payload_logging_disabled": bool(events)
        and all(event["raw_payload_logged"] is False for event in events),
        "official_eval_identifiers_not_runtime_visible": bool(events)
        and all(event["official_eval_identifiers_runtime_visible"] is False for event in events),
    }


def protocol_v1_dry_run_report(plan: dict[str, Any]) -> dict[str, Any]:
    events = protocol_v1_dry_run_events(plan)
    gates = protocol_v1_dry_run_gates(plan, events)
    prescription_counts: dict[str, int] = {}
    for event in events:
        prescription_id = str(event["prescription_id"])
        prescription_counts[prescription_id] = prescription_counts.get(prescription_id, 0) + 1
    return generate_report(
        phase="6.protocol_v1_dry_run_gate",
        decision=PASS_DECISION if all(gates.values()) else BLOCKED_DECISION,
        gate_results=gates,
        extras={
            "version": PROTOCOL_V1_DRY_RUN_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "summary": {
                "pair_count": len(plan.get("pairs") or []),
                "event_count": len(events),
                "prescription_counts": dict(sorted(prescription_counts.items())),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "same_pair_positive_claim_allowed": False,
                "live_execution_authorized": False,
            },
            "continuation_policy": {
                "allow_protocol_v1_live_hook_adapter_preflight": all(gates.values()),
                "allow_protocol_v1_real_run": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "implement_protocol_v1_live_hook_adapter_preflight_on_fresh_candidates"
                    if all(gates.values())
                    else "fix_protocol_v1_plan_before_live_adapter_preflight"
                ),
            },
        },
    )


def write_protocol_v1_dry_run_evidence(
    *,
    protocol_v1_plan: dict[str, Any],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = protocol_v1_dry_run_report(protocol_v1_plan)
    events = protocol_v1_dry_run_events(protocol_v1_plan)
    report_path = output_dir / "protocol_v1_dry_run_report.json"
    manifest_path = output_dir / "protocol_v1_dry_run_manifest.json"
    events_path = output_dir / "protocol_v1_dry_run_events.jsonl"
    summary_path = output_dir / "protocol_v1_dry_run_summary.json"

    write_jsonl(events_path, events)
    summary = {
        "decision": report["decision"],
        "passed": report["passed"],
        "summary": report["summary"],
        "continuation_policy": report["continuation_policy"],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    artifacts = [_artifact(path) for path in [report_path, events_path, summary_path]]
    artifacts.extend(_artifact(path) for path in input_artifacts or [])
    manifest = generate_manifest(
        phase="6.protocol_v1_dry_run_gate",
        report=report,
        artifacts=artifacts,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "report": report,
        "events": events,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "events_path": events_path,
        "summary_path": summary_path,
    }
