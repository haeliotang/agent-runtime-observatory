"""State inference for Protocol v2 batch orchestration.

Disk artifacts are the single source of truth.  No live execution, Docker,
provider calls, or official-eval is ever triggered from this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Ordered happy-path states
STATES = (
    "pending",
    "preflight_ready",
    "awaiting_control_authorization",
    "control_complete",
    "awaiting_treatment_authorization",
    "treatment_complete",
    "pair_assembled",
    "awaiting_official_eval_authorization",
    "official_eval_complete",
)

# Terminal failure states (not in the happy-path tuple)
FAILURE_STATES = (
    "failed_preflight",
    "failed_control",
    "failed_treatment",
    "failed_pair_assembly",
    "failed_official_eval",
)

ALL_STATES = STATES + FAILURE_STATES


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file; return empty dict on missing/broken file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _official_eval_report(evidence_root: Path, source_task_id: str) -> Path:
    return (
        evidence_root
        / "protocol_v2_official_eval"
        / source_task_id
        / "protocol_v2_official_eval_report.json"
    )


def _pair_report(evidence_root: Path, source_task_id: str) -> Path:
    return (
        evidence_root
        / "protocol_v2_live_pair"
        / source_task_id
        / "protocol_v2_live_pair_report.json"
    )


def _live_single_report(evidence_root: Path, source_task_id: str, arm: str) -> Path:
    """Return the path to the arm-level live-single report.

    The arm directories are written by sweagent_protocol_v2_live directly
    under ``protocol_v2_live_single_executed/<source_task_id>/<arm>/``
    (as referenced in the live-pair report's control_dir/treatment_dir fields).
    We also fall back to ``protocol_v2_live_pair/<source_task_id>/<arm>/``.
    """
    candidates = [
        evidence_root
        / "protocol_v2_live_single_executed"
        / source_task_id
        / arm
        / "protocol_v2_live_single_report.json",
        evidence_root
        / "protocol_v2_live_pair"
        / source_task_id
        / arm
        / "protocol_v2_live_single_report.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    # Return the first candidate as canonical even if it doesn't exist
    return candidates[0]


def _preflight_report(evidence_root: Path, source_task_id: str) -> Path:
    return (
        evidence_root
        / "protocol_v2_planned_preflight"
        / source_task_id
        / "protocol_v2_planned_preflight_report.json"
    )


def _arm_complete(report: dict[str, Any], arm: str) -> bool:
    """Return True if a live-single report signals a completed run for this arm."""
    return (
        report.get("decision") == "protocol_v2_live_single_run_completed"
        and report.get("passed") is True
        and report.get("arm_type") == arm
    )


def infer_pair_state(
    pair_entry: dict[str, Any],
    evidence_root: Path,
) -> str:
    """Infer the current state of a pair from on-disk artifacts.

    Checks are performed from most-advanced to least-advanced so the first
    matching condition wins.
    """
    sid = pair_entry["source_task_id"]
    evidence_root = Path(evidence_root)

    # --- Official eval ---
    oe_report_path = _official_eval_report(evidence_root, sid)
    if oe_report_path.is_file():
        oe_report = _load_json(oe_report_path)
        # Passed = both arm evals completed successfully
        if oe_report.get("official_eval_completed") is True or oe_report.get("passed") is True:
            return "official_eval_complete"
        # Report exists but didn't pass
        return "failed_official_eval"

    # --- Pair assembly ---
    pair_rpt_path = _pair_report(evidence_root, sid)
    if pair_rpt_path.is_file():
        pair_rpt = _load_json(pair_rpt_path)
        if (
            pair_rpt.get("decision") == "protocol_v2_live_pair_ready_pending_official_eval"
            and pair_rpt.get("passed") is True
        ):
            return "awaiting_official_eval_authorization"
        if pair_rpt.get("decision") == "protocol_v2_live_pair_outcome_label_ready":
            # Outcomes already present without official eval report — treat as assembled
            return "awaiting_official_eval_authorization"
        if pair_rpt.get("decision") == "protocol_v2_live_pair_blocked":
            return "failed_pair_assembly"
        # Report exists but is otherwise incomplete
        return "pair_assembled"

    # --- Treatment arm ---
    treatment_rpt_path = _live_single_report(evidence_root, sid, "treatment")
    if treatment_rpt_path.is_file():
        treatment_rpt = _load_json(treatment_rpt_path)
        if _arm_complete(treatment_rpt, "treatment"):
            return "treatment_complete"
        # Report present but not marked complete
        return "failed_treatment"

    # --- Control arm ---
    control_rpt_path = _live_single_report(evidence_root, sid, "control")
    if control_rpt_path.is_file():
        control_rpt = _load_json(control_rpt_path)
        if _arm_complete(control_rpt, "control"):
            return "awaiting_treatment_authorization"
        return "failed_control"

    # --- Preflight ---
    pf_report_path = _preflight_report(evidence_root, sid)
    if pf_report_path.is_file():
        pf_report = _load_json(pf_report_path)
        if (
            pf_report.get("decision")
            == "protocol_v2_planned_preflight_ready_live_execution_not_authorized"
            and pf_report.get("passed") is True
        ):
            return "awaiting_control_authorization"
        if pf_report.get("passed") is True:
            # Passed preflight but different decision text — still treat as ready
            return "preflight_ready"
        return "failed_preflight"

    return "pending"
