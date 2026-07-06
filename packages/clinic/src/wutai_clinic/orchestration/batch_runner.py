"""Batch orchestration runner for Protocol v2 paired intervention experiments.

CLAIM BOUNDARY (written to every batch_state.json and status output):
    "This orchestrator advances offline packaging steps and surfaces operator
    commands only. It never authorizes or performs live execution, Docker,
    provider calls, or official eval, and batch progress implies no uplift,
    predictive, or causal claim."

HIGHEST PRINCIPLE: This module never passes docker/provider/official-eval
authorization flags in any code path.  Those flags only appear as literal
strings inside operator-command templates that are returned for human copy-paste
and never executed by this module.
"""
from __future__ import annotations

import json
import datetime as _dt

# datetime.UTC was added in Python 3.11; provide a fallback for 3.10.
try:
    from datetime import UTC as _UTC
except ImportError:
    _UTC = _dt.timezone.utc  # type: ignore[assignment]
from pathlib import Path
from typing import Any, Callable

from wutai_clinic.orchestration.state_inference import infer_pair_state

CLAIM_BOUNDARY = (
    "This orchestrator advances offline packaging steps and surfaces operator commands only. "
    "It never authorizes or performs live execution, Docker, provider calls, or official eval, "
    "and batch progress implies no uplift, predictive, or causal claim."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return _dt.datetime.now(_UTC).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _append_event(events_path: Path, event: dict[str, Any]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _load_spec(spec_path: Path) -> dict[str, Any]:
    return json.loads(spec_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Operator command builders (NEVER executed — returned as strings for humans)
# ---------------------------------------------------------------------------


def _control_auth_command(pair_entry: dict[str, Any]) -> str:
    sid = pair_entry["source_task_id"]
    pair_id = pair_entry.get("pair_id", "")
    pair_inputs_dir = pair_entry.get("pair_inputs_dir", "")
    output_root = pair_entry.get("output_root", "")
    protocol_template = pair_entry.get("protocol_template", "")
    return (
        f"wutai-clinic sweagent-protocol-v2-live-single"
        f" --source-task-id {sid}"
        f" --arm control"
        f" --pair-inputs-dir {pair_inputs_dir}"
        f" --protocol-template {protocol_template}"
        f" --output-root {output_root}"
        f" --pair-id {pair_id}"
        f" --execute --ack-docker --ack-external-provider"
    )


def _treatment_auth_command(pair_entry: dict[str, Any]) -> str:
    sid = pair_entry["source_task_id"]
    pair_id = pair_entry.get("pair_id", "")
    pair_inputs_dir = pair_entry.get("pair_inputs_dir", "")
    output_root = pair_entry.get("output_root", "")
    protocol_template = pair_entry.get("protocol_template", "")
    return (
        f"wutai-clinic sweagent-protocol-v2-live-single"
        f" --source-task-id {sid}"
        f" --arm treatment"
        f" --pair-inputs-dir {pair_inputs_dir}"
        f" --protocol-template {protocol_template}"
        f" --output-root {output_root}"
        f" --pair-id {pair_id}"
        f" --execute --ack-docker --ack-external-provider"
    )


def _official_eval_auth_command(pair_entry: dict[str, Any], evidence_root: Path) -> str:
    sid = pair_entry["source_task_id"]
    pair_dir = evidence_root / "protocol_v2_live_pair" / sid
    output_dir = evidence_root / "protocol_v2_official_eval" / sid
    return (
        f"wutai-clinic sweagent-protocol-v2-official-eval {pair_dir}"
        f" --output-dir {output_dir}"
        f" --run-official-eval --ack-official-eval"
    )


# ---------------------------------------------------------------------------
# Offline automation stubs
# (Only call Python functions that DON'T require ack flags)
# ---------------------------------------------------------------------------


def _default_command_runner(fn: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Default runner: call fn(**kwargs) directly."""
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Core: advance_batch
# ---------------------------------------------------------------------------


def advance_batch(
    spec: dict[str, Any],
    state_dir: Path,
    command_runner: Callable[[Callable[..., Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Advance all pairs in the batch by one state.

    Offline steps are executed via ``command_runner`` (or directly when not
    provided).  Authorization-gated steps are surfaced as operator_actions and
    never executed.

    Returns a summary dict with pairs_advanced, operator_actions, and
    pairs_status.
    """
    runner = command_runner or _default_command_runner

    batch_id = spec.get("batch_id", "unknown")
    evidence_root = Path(spec["evidence_root"])
    pairs = spec.get("pairs", [])

    state_dir.mkdir(parents=True, exist_ok=True)
    events_path = state_dir / "batch_events.jsonl"

    pairs_advanced: list[str] = []
    operator_actions: list[dict[str, Any]] = []
    pairs_status: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for pair_entry in pairs:
        pair_id = pair_entry["pair_id"]
        sid = pair_entry["source_task_id"]
        current_state = infer_pair_state(pair_entry, evidence_root)

        next_state: str | None = None
        action_taken: str | None = None
        op_action: dict[str, Any] | None = None

        try:
            if current_state == "awaiting_control_authorization":
                # Gate: human must execute control arm
                op_action = {
                    "pair_id": pair_id,
                    "state": current_state,
                    "command": _control_auth_command(pair_entry),
                }

            elif current_state == "awaiting_treatment_authorization":
                # Gate: human must execute treatment arm
                op_action = {
                    "pair_id": pair_id,
                    "state": current_state,
                    "command": _treatment_auth_command(pair_entry),
                }

            elif current_state == "treatment_complete":
                # Offline: assemble pair from completed arms
                from wutai_clinic.adapters.sweagent_protocol_v2_pair import (
                    SWEAgentProtocolV2LivePairSpec,
                    run_sweagent_protocol_v2_live_pair,
                )
                # Locate arm dirs and patch archives from the live-single reports
                control_dir, treatment_dir = _find_arm_dirs(evidence_root, sid)
                control_patch = control_dir / "sweagent_protocol_v2_live_single.patch"
                treatment_patch = treatment_dir / "sweagent_protocol_v2_live_single.patch"
                pair_output_dir = evidence_root / "protocol_v2_live_pair" / sid
                assembly_spec = SWEAgentProtocolV2LivePairSpec(
                    control_dir=control_dir,
                    treatment_dir=treatment_dir,
                    output_dir=pair_output_dir,
                    control_patch=control_patch,
                    treatment_patch=treatment_patch,
                )
                runner(run_sweagent_protocol_v2_live_pair, {"spec": assembly_spec})
                next_state = infer_pair_state(pair_entry, evidence_root)
                action_taken = "pair_assembled_offline"
                pairs_advanced.append(pair_id)

            elif current_state == "pair_assembled":
                # Re-infer — pair_assembled transitions to awaiting_official_eval_authorization
                # This state shouldn't persist long; treat as needing re-check.
                next_state = infer_pair_state(pair_entry, evidence_root)
                if next_state == "pair_assembled":
                    # Force official eval authorization prompt
                    op_action = {
                        "pair_id": pair_id,
                        "state": current_state,
                        "command": _official_eval_auth_command(pair_entry, evidence_root),
                    }
                action_taken = "recheck"

            elif current_state == "awaiting_official_eval_authorization":
                # Gate: human must run official eval
                op_action = {
                    "pair_id": pair_id,
                    "state": current_state,
                    "command": _official_eval_auth_command(pair_entry, evidence_root),
                }

            # Terminal / no-op states
            elif current_state in (
                "official_eval_complete",
                "failed_preflight",
                "failed_control",
                "failed_treatment",
                "failed_pair_assembly",
                "failed_official_eval",
                "pending",
                "preflight_ready",
                "control_complete",
                "treatment_complete",
            ):
                # pending/preflight_ready/control_complete: preflight is an
                # offline step but requires the pair-inputs dir inputs to be
                # present; we surface these as no-ops here and expect the
                # operator to have already run preflight or the control arm.
                pass

        except Exception as exc:
            error_event = {
                "event_type": "advance_error",
                "timestamp": _utc_now(),
                "batch_id": batch_id,
                "pair_id": pair_id,
                "state": current_state,
                "error": f"{type(exc).__name__}: {exc}",
            }
            _append_event(events_path, error_event)
            errors.append(error_event)
            next_state = current_state

        if op_action:
            operator_actions.append(op_action)

        # Re-infer final state after any offline step
        final_state = infer_pair_state(pair_entry, evidence_root)

        # Write event
        event: dict[str, Any] = {
            "event_type": "pair_state_check",
            "timestamp": _utc_now(),
            "batch_id": batch_id,
            "pair_id": pair_id,
            "source_task_id": sid,
            "state_before": current_state,
            "state_after": final_state,
            "action_taken": action_taken,
            "operator_action_queued": op_action is not None,
        }
        _append_event(events_path, event)

        pairs_status.append(
            {
                "pair_id": pair_id,
                "source_task_id": sid,
                "state": final_state,
                "next_step": _next_step_description(final_state),
            }
        )

    # State counts
    state_counts: dict[str, int] = {}
    for row in pairs_status:
        state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1

    summary = {
        "batch_id": batch_id,
        "generated_at": _utc_now(),
        "claim_boundary": CLAIM_BOUNDARY,
        "pairs_advanced": pairs_advanced,
        "operator_actions": operator_actions,
        "pairs_status": pairs_status,
        "state_counts": state_counts,
        "errors": errors,
    }

    # Write snapshot (always overwritten — events_path is the append-only log)
    _write_json(state_dir / "batch_state.json", summary)

    return summary


# ---------------------------------------------------------------------------
# Core: batch_status  (read-only)
# ---------------------------------------------------------------------------


def batch_status(
    spec: dict[str, Any],
    state_dir: Path,
) -> dict[str, Any]:
    """Return a read-only status summary.  Never writes, never executes."""
    batch_id = spec.get("batch_id", "unknown")
    evidence_root = Path(spec["evidence_root"])
    pairs = spec.get("pairs", [])

    pairs_status: list[dict[str, Any]] = []
    pending_op_actions: list[dict[str, Any]] = []

    for pair_entry in pairs:
        pair_id = pair_entry["pair_id"]
        sid = pair_entry["source_task_id"]
        state = infer_pair_state(pair_entry, evidence_root)
        next_step = _next_step_description(state)

        if state == "awaiting_control_authorization":
            pending_op_actions.append(
                {
                    "pair_id": pair_id,
                    "state": state,
                    "command": _control_auth_command(pair_entry),
                }
            )
        elif state == "awaiting_treatment_authorization":
            pending_op_actions.append(
                {
                    "pair_id": pair_id,
                    "state": state,
                    "command": _treatment_auth_command(pair_entry),
                }
            )
        elif state in ("awaiting_official_eval_authorization", "pair_assembled"):
            pending_op_actions.append(
                {
                    "pair_id": pair_id,
                    "state": state,
                    "command": _official_eval_auth_command(pair_entry, evidence_root),
                }
            )

        pairs_status.append(
            {
                "pair_id": pair_id,
                "source_task_id": sid,
                "state": state,
                "next_step": next_step,
            }
        )

    state_counts: dict[str, int] = {}
    for row in pairs_status:
        state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1

    return {
        "batch_id": batch_id,
        "generated_at": _utc_now(),
        "claim_boundary": CLAIM_BOUNDARY,
        "pairs_status": pairs_status,
        "state_counts": state_counts,
        "pending_operator_actions": pending_op_actions,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _next_step_description(state: str) -> str:
    _map = {
        "pending": "run_protocol_v2_planned_preflight",
        "preflight_ready": "awaiting_control_authorization",
        "awaiting_control_authorization": "operator_must_execute_control_arm",
        "control_complete": "awaiting_treatment_authorization",
        "awaiting_treatment_authorization": "operator_must_execute_treatment_arm",
        "treatment_complete": "run_pair_assembly_offline",
        "pair_assembled": "awaiting_official_eval_authorization",
        "awaiting_official_eval_authorization": "operator_must_run_official_eval",
        "official_eval_complete": "complete",
        "failed_preflight": "fix_preflight_inputs_and_rerun",
        "failed_control": "clear_control_artifacts_and_retry",
        "failed_treatment": "clear_treatment_artifacts_and_retry",
        "failed_pair_assembly": "clear_pair_artifacts_and_retry",
        "failed_official_eval": "investigate_official_eval_failure",
    }
    return _map.get(state, "unknown")


def _find_arm_dirs(evidence_root: Path, source_task_id: str) -> tuple[Path, Path]:
    """Locate control and treatment arm output directories.

    The live-single adapter writes to
    ``protocol_v2_live_single_executed/<source_task_id>/<arm>/``.
    Fall back to reading the pair report if present.
    """
    base = evidence_root / "protocol_v2_live_single_executed" / source_task_id
    control_dir = base / "control"
    treatment_dir = base / "treatment"
    if control_dir.is_dir() and treatment_dir.is_dir():
        return control_dir, treatment_dir

    # Try to read from live-pair report if it exists
    pair_rpt_path = (
        evidence_root / "protocol_v2_live_pair" / source_task_id / "protocol_v2_live_pair_report.json"
    )
    if pair_rpt_path.is_file():
        try:
            rpt = json.loads(pair_rpt_path.read_text(encoding="utf-8"))
            ctrl = rpt.get("control_dir")
            trt = rpt.get("treatment_dir")
            if ctrl and trt:
                return Path(ctrl), Path(trt)
        except Exception:
            pass

    # Canonical fallback even if directories don't exist yet
    return control_dir, treatment_dir
