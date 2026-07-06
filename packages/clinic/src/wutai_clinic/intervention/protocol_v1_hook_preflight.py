from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.intervention.protocol_v1 import ProtocolV1
from wutai_clinic.intervention.protocol_v1_hook import ProtocolV1ConstraintHook
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V1_HOOK_PREFLIGHT_PHASE = "6.protocol_v1_constraint_hook_preflight"
PROTOCOL_V1_HOOK_PREFLIGHT_VERSION = "phase6_protocol_v1_constraint_hook_preflight_v1"
EXPECTED_ADAPTER_PREFLIGHT_DECISION = "protocol_v1_sweagent_adapter_preflight_ready_no_run"
EXPECTED_PLAN_DECISION = "protocol_v1_plan_ready_not_live_executed"
PASS_DECISION = "protocol_v1_constraint_hook_preflight_passed_no_live_run"
BLOCKED_DECISION = "protocol_v1_constraint_hook_preflight_blocked"
BOUNDARY = (
    "Protocol v1 constraint-hook preflight executes a controlled in-process harness only. "
    "It proves hook-level blocking semantics without starting Docker, calling a provider, "
    "running official eval, or authorizing same-pair positive attribution."
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


def _targeted_harness(hook: ProtocolV1ConstraintHook) -> None:
    hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")
    hook.before_action("cd /testbed && python reproduce_failure.py")
    hook.after_action(
        "cd /testbed && python reproduce_failure.py",
        "Traceback ... AssertionError: failed",
    )
    hook.before_action("str_replace_editor str_replace /testbed/src/pkg.py")
    hook.after_action("str_replace_editor str_replace /testbed/src/pkg.py", "edited")
    hook.before_action("submit")
    hook.before_action("cd /testbed && python reproduce_failure.py")
    hook.after_action("cd /testbed && python reproduce_failure.py", "1 passed")
    hook.before_action("submit")


def _regression_harness(hook: ProtocolV1ConstraintHook) -> None:
    hook.before_action("str_replace_editor str_replace /testbed/lib/matplotlib/colors.py")
    hook.after_action(
        "str_replace_editor str_replace /testbed/lib/matplotlib/colors.py",
        "edited",
    )
    hook.before_action("submit")
    hook.before_action("cd /testbed && pytest target")
    hook.after_action("cd /testbed && pytest target", "1 passed")
    hook.before_action("submit")
    hook.before_action("cd /testbed && pytest guard regression")
    hook.after_action(
        "cd /testbed && pytest guard regression",
        "PASS_TO_FAIL: guard regression failed",
    )
    hook.before_action("submit")


def protocol_v1_hook_preflight_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row_index, row in enumerate(plan.get("pairs") or []):
        try:
            protocol = ProtocolV1.from_dict(row.get("protocol_v1") or {})
        except ValueError as exc:
            events.append(
                {
                    "event": "protocol_v1_hook_preflight_invalid_protocol",
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
        hook = ProtocolV1ConstraintHook(
            protocol=protocol,
            source_task_id=str(row.get("source_task_id") or ""),
            pair_id=str(row.get("pair_id") or ""),
        )
        if protocol.action.prescription_id == "targeted_failure_oracle":
            _targeted_harness(hook)
        elif protocol.action.prescription_id == "regression_guarded_patch_validation":
            _regression_harness(hook)
        else:  # pragma: no cover - ProtocolV1 validation prevents this.
            continue
        for event in hook.audit_events:
            events.append(
                {
                    **event,
                    "row_index": row_index,
                    "controlled_harness": True,
                    "official_eval_identifiers_runtime_visible": False,
                }
            )
    return events


def _event_count(events: list[dict[str, Any]], *, constraint_id: str, blocked: bool) -> int:
    return sum(
        1
        for event in events
        if event.get("constraint_id") == constraint_id and event.get("blocked") is blocked
    )


def protocol_v1_hook_preflight_gates(
    *,
    plan: dict[str, Any],
    adapter_preflight_report: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, bool]:
    pairs = plan.get("pairs") or []
    prescription_counts: dict[str, int] = {}
    for row in pairs:
        action = (row.get("protocol_v1") or {}).get("action") or {}
        prescription = str(action.get("prescription_id") or "")
        prescription_counts[prescription] = prescription_counts.get(prescription, 0) + 1
    targeted_count = prescription_counts.get("targeted_failure_oracle", 0)
    regression_count = prescription_counts.get("regression_guarded_patch_validation", 0)
    adapter_policy = adapter_preflight_report.get("continuation_policy") or {}
    return {
        "adapter_preflight_passed": adapter_preflight_report.get("passed") is True,
        "adapter_preflight_decision_expected": (
            adapter_preflight_report.get("decision") == EXPECTED_ADAPTER_PREFLIGHT_DECISION
        ),
        "adapter_allows_hook_implementation": (
            adapter_policy.get("allow_protocol_v1_constraint_hook_implementation") is True
        ),
        "adapter_still_blocks_real_run": (
            adapter_policy.get("allow_protocol_v1_real_run") is False
        ),
        "plan_decision_expected": plan.get("decision") == EXPECTED_PLAN_DECISION,
        "pairs_present": len(pairs) > 0,
        "events_cover_all_pairs": len({event.get("row_index") for event in events}) == len(pairs),
        "hook_events_present": len(events) > 0,
        "edit_before_failure_blocked_for_targeted": _event_count(
            events,
            constraint_id="block_edit_until_failure_reproduced_or_explained",
            blocked=True,
        )
        >= targeted_count,
        "submit_before_target_recheck_blocked": _event_count(
            events,
            constraint_id="require_post_patch_target_recheck",
            blocked=True,
        )
        >= len(pairs),
        "submit_before_guard_recheck_blocked_for_regression": _event_count(
            events,
            constraint_id="require_post_patch_guard_recheck",
            blocked=True,
        )
        >= regression_count,
        "submit_on_guard_regression_blocked": _event_count(
            events,
            constraint_id="block_submit_on_guard_regression",
            blocked=True,
        )
        >= regression_count,
        "hook_events_no_runtime_started": bool(events)
        and all(
            event["runner_started"] is False
            and event["model_call_started"] is False
            and event["docker_or_official_eval_started"] is False
            for event in events
        ),
        "official_eval_identifiers_not_runtime_visible": bool(events)
        and all(
            event.get("official_eval_identifiers_runtime_visible") is False for event in events
        ),
        "raw_payload_logging_disabled": bool(events)
        and all(event["raw_payload_logged"] is False for event in events),
    }


def protocol_v1_hook_preflight_report(
    *,
    plan: dict[str, Any],
    adapter_preflight_report: dict[str, Any],
) -> dict[str, Any]:
    events = protocol_v1_hook_preflight_events(plan)
    gates = protocol_v1_hook_preflight_gates(
        plan=plan,
        adapter_preflight_report=adapter_preflight_report,
        events=events,
    )
    blocked_counts: dict[str, int] = {}
    for event in events:
        if event.get("blocked") is True and event.get("constraint_id"):
            key = str(event["constraint_id"])
            blocked_counts[key] = blocked_counts.get(key, 0) + 1
    return generate_report(
        phase=PROTOCOL_V1_HOOK_PREFLIGHT_PHASE,
        decision=PASS_DECISION if all(gates.values()) else BLOCKED_DECISION,
        gate_results=gates,
        extras={
            "version": PROTOCOL_V1_HOOK_PREFLIGHT_VERSION,
            "claim_boundary": BOUNDARY,
            "summary": {
                "pair_count": len(plan.get("pairs") or []),
                "hook_event_count": len(events),
                "blocking_event_count": sum(1 for event in events if event.get("blocked") is True),
                "blocked_constraint_counts": dict(sorted(blocked_counts.items())),
                "runner_started": False,
                "model_call_started": False,
                "docker_or_official_eval_started": False,
                "live_execution_authorized": False,
            },
            "continuation_policy": {
                "allow_protocol_v1_live_single_adapter_integration": all(gates.values()),
                "allow_protocol_v1_real_run": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "wire_protocol_v1_constraint_hook_into_sweagent_live_single_adapter"
                    if all(gates.values())
                    else "fix_protocol_v1_constraint_hook_before_adapter_integration"
                ),
            },
        },
    )


def write_protocol_v1_hook_preflight_evidence(
    *,
    protocol_v1_plan: dict[str, Any],
    adapter_preflight_report: dict[str, Any],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    events = protocol_v1_hook_preflight_events(protocol_v1_plan)
    report = protocol_v1_hook_preflight_report(
        plan=protocol_v1_plan,
        adapter_preflight_report=adapter_preflight_report,
    )
    report_path = output_dir / "protocol_v1_constraint_hook_preflight_report.json"
    manifest_path = output_dir / "protocol_v1_constraint_hook_preflight_manifest.json"
    events_path = output_dir / "protocol_v1_constraint_hook_preflight_events.jsonl"
    summary_path = output_dir / "protocol_v1_constraint_hook_preflight_summary.json"

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
    artifacts = [_artifact(path) for path in [report_path, events_path, summary_path]]
    artifacts.extend(_artifact(path) for path in input_artifacts or [])
    manifest = generate_manifest(
        phase=PROTOCOL_V1_HOOK_PREFLIGHT_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V1_HOOK_PREFLIGHT_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "events": events,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "events_path": events_path,
        "summary_path": summary_path,
    }
