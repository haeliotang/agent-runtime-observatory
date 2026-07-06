from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.intervention.protocol_v1_batch_outcomes import NO_UPLIFT_LABELS
from wutai_clinic.intervention.protocol_v2 import ProtocolV2
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V2_DRY_RUN_PHASE = "6.protocol_v2_dry_run_gate"
PROTOCOL_V2_DRY_RUN_VERSION = "phase6_protocol_v2_dry_run_gate_v1"
PASS_DECISION = "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
BLOCKED_DECISION = "protocol_v2_dry_run_gate_blocked"
CLAIM_BOUNDARY = (
    "Protocol v2 dry-run converts observed Protocol v1 no-uplift dynamics into a "
    "prospective prescription plan. It does not rerun the same pair for positive "
    "attribution, start Docker, call a provider, run official eval, or inject official "
    "test/outcome identifiers into runtime context."
)


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


def _target_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("protocol_family") == "protocol_v1_constraint_hook"
        and row.get("official_eval_completed") is True
        and row.get("effect_label") in NO_UPLIFT_LABELS
        and row.get("trajectory_outcome_class") == "trajectory_diverged_no_uplift"
    ]


def build_protocol_v2_prescription_plan(
    *,
    batch_outcomes_report: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
    protocol_v2: ProtocolV2,
) -> dict[str, Any]:
    targets = _target_rows(outcome_rows)
    rows = []
    for index, row in enumerate(targets, start=1):
        rows.append(
            {
                "phase": PROTOCOL_V2_DRY_RUN_PHASE,
                "plan_rank": index,
                "source_pair_id": row.get("pair_id"),
                "source_task_id": row.get("source_task_id"),
                "source_protocol_family": row.get("protocol_family"),
                "source_effect_label": row.get("effect_label"),
                "source_trajectory_outcome_class": row.get("trajectory_outcome_class"),
                "prescription_reason": "behavior_changed_without_official_outcome_uplift",
                "protocol_v2": protocol_v2.to_dict(),
                "protocol_hash": protocol_v2.protocol_hash,
                "runtime_oracle_source": "live_feature_and_prefix_observation_only",
                "same_pair_positive_claim_allowed": False,
                "live_execution_authorized": False,
                "official_eval_authorized": False,
                "requires_new_fresh_failure_target": True,
            }
        )
    return {
        "decision": "protocol_v2_prescription_plan_ready_not_live_executed"
        if rows
        else "protocol_v2_prescription_plan_blocked_no_target_rows",
        "source_decision": batch_outcomes_report.get("decision"),
        "source_passed": batch_outcomes_report.get("passed"),
        "claim_boundary": CLAIM_BOUNDARY,
        "pair_count": len(rows),
        "same_pair_positive_claim_allowed": False,
        "live_execution_authorized": False,
        "official_eval_authorized": False,
        "pairs": rows,
    }


def protocol_v2_dry_run_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for index, row in enumerate(plan.get("pairs") or []):
        protocol_error = None
        protocol_hash = None
        prescription_id = "invalid_protocol"
        steps: list[str] = []
        prompt_style = None
        try:
            protocol = ProtocolV2.from_dict(row.get("protocol_v2") or {})
            protocol_hash = protocol.protocol_hash
            prescription_id = protocol.action.prescription_id
            steps = list(protocol.action.steps)
            prompt_style = protocol.action.prompt_style
        except ValueError as exc:
            protocol_error = str(exc)
        events.append(
            {
                "event": "protocol_v2_prescription_dry_run",
                "row_index": index,
                "source_task_id": row.get("source_task_id"),
                "source_pair_id": row.get("source_pair_id"),
                "protocol_valid": protocol_error is None,
                "protocol_error": protocol_error,
                "protocol_hash": protocol_hash,
                "prescription_id": prescription_id,
                "steps": steps,
                "prompt_style": prompt_style,
                "runtime_oracle_source": row.get("runtime_oracle_source"),
                "same_pair_positive_claim_allowed": row.get("same_pair_positive_claim_allowed"),
                "requires_new_fresh_failure_target": row.get("requires_new_fresh_failure_target"),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "raw_payload_logged": False,
            }
        )
    return events


def _all_pairs(plan: dict[str, Any], predicate: Any) -> bool:
    pairs = plan.get("pairs") or []
    return bool(pairs) and all(predicate(row) for row in pairs)


def protocol_v2_dry_run_gates(
    *,
    batch_outcomes_report: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
    plan: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, bool]:
    protocol_rows_valid = True
    protocol_hashes_match = True
    for row in plan.get("pairs") or []:
        try:
            protocol = ProtocolV2.from_dict(row.get("protocol_v2") or {})
        except ValueError:
            protocol_rows_valid = False
            protocol_hashes_match = False
            continue
        protocol_hashes_match = (
            protocol_hashes_match and row.get("protocol_hash") == protocol.protocol_hash
        )
    target_rows = _target_rows(outcome_rows)
    payload_audit_projection = {
        "plan": {
            key: value
            for key, value in plan.items()
            if key != "pairs"
        },
        "pairs": [
            {key: value for key, value in row.items() if key != "protocol_v2"}
            for row in plan.get("pairs") or []
        ],
        "events": events,
    }
    acceptable_source_decisions = {
        "protocol_v1_batch_outcomes_underpowered_no_uplift_observed",
        "protocol_v1_batch_outcomes_no_uplift_needs_prescription_revision",
    }
    return {
        "source_batch_outcomes_report_passed": batch_outcomes_report.get("passed") is True,
        "source_decision_supports_prescription_revision": batch_outcomes_report.get("decision")
        in acceptable_source_decisions,
        "target_rows_present": len(target_rows) > 0,
        "target_rows_match_plan_count": len(target_rows) == len(plan.get("pairs") or []),
        "target_rows_are_protocol_v1_no_uplift_dynamics": bool(target_rows)
        and all(
            row.get("effect_label") in NO_UPLIFT_LABELS
            and row.get("trajectory_outcome_class") == "trajectory_diverged_no_uplift"
            for row in target_rows
        ),
        "same_pair_positive_claim_blocked": plan.get("same_pair_positive_claim_allowed") is False
        and _all_pairs(plan, lambda row: row.get("same_pair_positive_claim_allowed") is False),
        "requires_new_fresh_failure_target": _all_pairs(
            plan, lambda row: row.get("requires_new_fresh_failure_target") is True
        ),
        "protocol_rows_valid": protocol_rows_valid,
        "protocol_hashes_match": protocol_hashes_match,
        "runtime_oracle_live_features_only": _all_pairs(
            plan,
            lambda row: row.get("runtime_oracle_source")
            == "live_feature_and_prefix_observation_only",
        ),
        "dry_run_event_count_matches_pairs": len(events) == len(plan.get("pairs") or []),
        "dry_run_started_no_model_or_runner": bool(events)
        and all(
            event["runner_started"] is False
            and event["model_call_started"] is False
            and event["docker_or_official_eval_started"] is False
            for event in events
        ),
        "raw_payload_logging_disabled": bool(events)
        and all(event["raw_payload_logged"] is False for event in events),
        "payload_has_no_raw_payload_keys": no_raw_payload(payload_audit_projection),
        "payload_has_no_secret_literals": no_secret_literal(payload_audit_projection),
        "live_execution_not_authorized": plan.get("live_execution_authorized") is False,
        "official_eval_not_authorized": plan.get("official_eval_authorized") is False,
    }


def protocol_v2_dry_run_report(
    *,
    batch_outcomes_report: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
    protocol_v2: ProtocolV2,
) -> dict[str, Any]:
    plan = build_protocol_v2_prescription_plan(
        batch_outcomes_report=batch_outcomes_report,
        outcome_rows=outcome_rows,
        protocol_v2=protocol_v2,
    )
    events = protocol_v2_dry_run_events(plan)
    gates = protocol_v2_dry_run_gates(
        batch_outcomes_report=batch_outcomes_report,
        outcome_rows=outcome_rows,
        plan=plan,
        events=events,
    )
    prescription_counts: dict[str, int] = {}
    for event in events:
        prescription_id = str(event["prescription_id"])
        prescription_counts[prescription_id] = prescription_counts.get(prescription_id, 0) + 1
    return generate_report(
        phase=PROTOCOL_V2_DRY_RUN_PHASE,
        decision=PASS_DECISION if all(gates.values()) else BLOCKED_DECISION,
        gate_results=gates,
        extras={
            "version": PROTOCOL_V2_DRY_RUN_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "summary": {
                "target_pair_count": len(plan.get("pairs") or []),
                "event_count": len(events),
                "prescription_counts": dict(sorted(prescription_counts.items())),
                "source_decision": batch_outcomes_report.get("decision"),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "live_execution_authorized": False,
                "official_eval_authorized": False,
            },
            "continuation_policy": {
                "allow_protocol_v2_live_single_planned_preflight": all(gates.values()),
                "allow_protocol_v2_real_run": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "collect_new_fresh_failure_targets_then_run_protocol_v2_planned_preflight"
                    if all(gates.values())
                    else "fix_protocol_v2_plan_before_live_adapter_preflight"
                ),
            },
        },
    )


def write_protocol_v2_dry_run_evidence(
    *,
    batch_outcomes_report: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
    protocol_v2: ProtocolV2,
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_protocol_v2_prescription_plan(
        batch_outcomes_report=batch_outcomes_report,
        outcome_rows=outcome_rows,
        protocol_v2=protocol_v2,
    )
    events = protocol_v2_dry_run_events(plan)
    report = protocol_v2_dry_run_report(
        batch_outcomes_report=batch_outcomes_report,
        outcome_rows=outcome_rows,
        protocol_v2=protocol_v2,
    )

    plan_path = output_dir / "protocol_v2_prescription_plan.json"
    rows_path = output_dir / "protocol_v2_prescription_rows.jsonl"
    events_path = output_dir / "protocol_v2_dry_run_events.jsonl"
    report_path = output_dir / "protocol_v2_dry_run_report.json"
    summary_path = output_dir / "protocol_v2_dry_run_summary.json"
    manifest_path = output_dir / "protocol_v2_dry_run_manifest.json"

    _write_json(plan_path, plan)
    write_jsonl(rows_path, list(plan.get("pairs") or []))
    write_jsonl(events_path, events)
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
    artifacts = [_artifact(path) for path in [plan_path, rows_path, events_path, report_path, summary_path]]
    artifacts.extend(_artifact(path) for path in input_artifacts or [])
    manifest = generate_manifest(
        phase=PROTOCOL_V2_DRY_RUN_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V2_DRY_RUN_VERSION
    _write_json(manifest_path, manifest)
    return {
        "plan": plan,
        "events": events,
        "report": report,
        "manifest": manifest,
        "plan_path": plan_path,
        "rows_path": rows_path,
        "events_path": events_path,
        "report_path": report_path,
        "summary_path": summary_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "PASS_DECISION",
    "PROTOCOL_V2_DRY_RUN_VERSION",
    "build_protocol_v2_prescription_plan",
    "protocol_v2_dry_run_report",
    "write_protocol_v2_dry_run_evidence",
]
