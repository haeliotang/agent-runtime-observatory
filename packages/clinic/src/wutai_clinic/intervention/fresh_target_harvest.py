"""Fresh-target harvest pipeline for Protocol v2.

Two phases:
  Plan mode  (default, pure offline) — write_fresh_target_harvest_plan(...)
  Execute mode (gated)               — run_fresh_target_harvest(...)

Contamination exclusion is a hard gate:
  - Any instance_id found in the evidence-index rows (regardless of status) is excluded.
  - Any instance_id found in the Lite300 report is excluded.
  - Missing evidence-index or Lite300 extraction failure MUST block; no degraded pass-through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

FRESH_HARVEST_PHASE = "task5.fresh_target_harvest"
CLAIM_BOUNDARY = (
    "This harvest package selects and runs uncontaminated baseline failure candidates only. "
    "Baseline failure status is not an official-eval outcome, and nothing in this package implies "
    "intervention uplift, predictive diagnosis, or generalized causal effect."
)

# Runner signature: (instance_id: str, output_dir: Path) -> dict
# Keys: instance_id, status ("resolved"|"unresolved"|"error"), patch_path, archive_dir
HarvestRunner = Callable[[str, Path], dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl" and path.is_file():
        with path.open("rb") as fh:
            record_count = sum(1 for line in fh if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": record_count,
        "exists": path.is_file(),
    }


def _load_evidence_index_ids(evidence_index_path: Path) -> set[str]:
    """Load all instance_id values from evidence_index_rows.jsonl.

    Raises FileNotFoundError if the file is missing; caller must treat this as
    a hard block (contamination exclusion cannot be performed).
    """
    if not evidence_index_path.exists():
        raise FileNotFoundError(
            f"Evidence index not found: {evidence_index_path}. "
            "Contamination exclusion cannot proceed."
        )
    ids: set[str] = set()
    with evidence_index_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                iid = row.get("instance_id")
                if iid:
                    ids.add(str(iid))
    return ids


def _load_dataset_instance_ids(dataset_instances_path: Path) -> list[str]:
    """Load instance ids from dataset file.

    Supports:
      - JSON object: {"instance_ids": [...]}
      - JSONL: one {"instance_id": "..."} per line
    """
    if not dataset_instances_path.exists():
        raise FileNotFoundError(f"Dataset instances file not found: {dataset_instances_path}")
    text = dataset_instances_path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    # Try as JSON object first
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            ids = payload.get("instance_ids") or payload.get("instance_id")
            if isinstance(ids, list):
                return [str(i) for i in ids if i]
        # JSON array of strings
        if isinstance(payload, list):
            return [str(i) for i in payload if i]
    except json.JSONDecodeError:
        pass
    # Try as JSONL
    ids_list: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                iid = row.get("instance_id") or row.get("source_task_id")
                if iid:
                    ids_list.append(str(iid))
            elif isinstance(row, str):
                ids_list.append(row)
        except json.JSONDecodeError:
            continue
    return ids_list


def _load_lite300_ids(lite300_report_path: Path) -> tuple[set[str], bool]:
    """Load instance_id values from the Lite300 report.

    Returns (ids, degraded):
      - degraded=False, ids=set(...) on success
      - degraded=True,  ids=set()   on any failure (file missing, key absent, parse error)
    """
    if not lite300_report_path.exists():
        return set(), True
    try:
        payload = json.loads(lite300_report_path.read_text(encoding="utf-8"))
    except Exception:
        return set(), True
    if not isinstance(payload, dict):
        return set(), True
    # Primary key: "completed_ids" (as seen in the actual Lite300 report).
    # Use explicit key presence check — do NOT use `or` chaining, which treats [] as falsy.
    ids_raw = None
    for key in ("completed_ids", "instance_ids", "ids"):
        if key in payload:
            ids_raw = payload[key]
            break
    if ids_raw is None:
        # Try to extract from nested structures (auto-detect list of "repo__task-id" strings)
        for val in payload.values():
            if isinstance(val, list) and val and isinstance(val[0], str) and "__" in val[0]:
                ids_raw = val
                break
    if ids_raw is None or not isinstance(ids_raw, list):
        return set(), True
    ids: set[str] = {str(i) for i in ids_raw if i}
    # An empty list is a valid result (no Lite300 contamination); only degrade on parse failure.
    return ids, False


def _source_family(instance_id: str) -> str:
    """Extract repo family prefix from instance_id (e.g. 'django__django-123' -> 'django')."""
    if "__" in instance_id:
        return instance_id.split("__")[0]
    return instance_id


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


def write_fresh_target_harvest_plan(
    evidence_index_path: Path,
    dataset_instances_path: Path,
    lite300_report_path: Path,
    max_instances: int,
    output_dir: Path,
    dataset_source: str = "princeton-nlp/SWE-bench_Verified",
) -> dict[str, Any]:
    """Offline plan: select uncontaminated instances from the dataset.

    This function performs NO external calls, Docker, or provider access.
    Returns the plan report dict and writes outputs to output_dir.

    Blocking conditions (decision = *_blocked_lite300_exclusion_degraded):
      - Lite300 report missing or key extraction fails
    Blocking conditions (raises):
      - Evidence index missing (FileNotFoundError)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Gate 1: load evidence index (hard block if missing) ---
    evidence_index_loaded = False
    evidence_ids: set[str] = set()
    try:
        evidence_ids = _load_evidence_index_ids(evidence_index_path)
        evidence_index_loaded = True
    except FileNotFoundError:
        raise

    # --- Gate 2: load Lite300 exclusion list ---
    lite300_ids, lite300_degraded = _load_lite300_ids(lite300_report_path)
    lite300_loaded = not lite300_degraded

    gates: dict[str, bool] = {
        "evidence_index_loaded": evidence_index_loaded,
        "lite300_loaded": lite300_loaded,
        "lite300_exclusion_degraded": lite300_degraded,
    }

    if lite300_degraded:
        decision = "fresh_target_harvest_plan_blocked_lite300_exclusion_degraded"
        report = generate_report(
            phase=FRESH_HARVEST_PHASE,
            decision=decision,
            gate_results={
                "evidence_index_loaded": evidence_index_loaded,
                "lite300_loaded": False,
                "lite300_exclusion_degraded_is_false": False,  # this gate fails → block
            },
            extras={
                "claim_boundary": CLAIM_BOUNDARY,
                "gates": gates,
                "operator_decisions": {
                    "dataset_source": dataset_source,
                    "max_instances": max_instances,
                    "dataset_instances_path": str(dataset_instances_path),
                    "evidence_index_path": str(evidence_index_path),
                },
                "summary": {
                    "selected_instances": [],
                    "excluded_by_evidence_index": len(evidence_ids),
                    "excluded_by_lite300": 0,
                    "max_instances_cap": max_instances,
                },
            },
        )
        report_path = output_dir / "fresh_target_harvest_plan.json"
        _write_json(report_path, report)
        manifest = generate_manifest(
            phase=FRESH_HARVEST_PHASE,
            report=report,
            artifacts=[_artifact(report_path)],
        )
        manifest_path = output_dir / "fresh_target_harvest_plan_manifest.json"
        _write_json(manifest_path, manifest)
        return {
            "decision": decision,
            "report": report,
            "report_path": report_path,
            "manifest_path": manifest_path,
            "selected_instances": [],
        }

    # --- Load dataset instances ---
    all_dataset_ids = _load_dataset_instance_ids(dataset_instances_path)

    # --- Exclusion logic ---
    combined_exclusion = evidence_ids | lite300_ids
    # Count per exclusion category (only for ids that are in the dataset)
    excluded_by_evidence: list[str] = []
    excluded_by_lite300: list[str] = []
    for iid in all_dataset_ids:
        if iid in evidence_ids:
            excluded_by_evidence.append(iid)
        elif iid in lite300_ids:
            excluded_by_lite300.append(iid)

    # Candidates = dataset ids NOT in either exclusion set, deterministic sort
    candidates = sorted(iid for iid in all_dataset_ids if iid not in combined_exclusion)

    # Apply max_instances cap
    selected_ids = candidates[:max_instances]

    selected_instances = [
        {
            "instance_id": iid,
            "selection_role": "harvest_candidate",
            "contamination_status": "uncontaminated",
            "source_family": _source_family(iid),
        }
        for iid in selected_ids
    ]

    decision = "fresh_target_harvest_plan_ready_live_execution_not_authorized"
    gates_for_report = {
        "evidence_index_loaded": True,
        "lite300_loaded": True,
        "lite300_exclusion_degraded_is_false": True,
        "candidates_found": len(selected_ids) > 0,
    }
    report = generate_report(
        phase=FRESH_HARVEST_PHASE,
        decision=decision,
        gate_results=gates_for_report,
        extras={
            "claim_boundary": CLAIM_BOUNDARY,
            "gates": gates,
            "operator_decisions": {
                "dataset_source": dataset_source,
                "max_instances": max_instances,
                "dataset_instances_path": str(dataset_instances_path),
                "evidence_index_path": str(evidence_index_path),
            },
            "summary": {
                "selected_instances": selected_instances,
                "excluded_by_evidence_index": len(excluded_by_evidence),
                "excluded_by_lite300": len(excluded_by_lite300),
                "max_instances_cap": max_instances,
            },
        },
    )
    # Override decision to the canonical value (generate_report doesn't override it)
    report["decision"] = decision

    report_path = output_dir / "fresh_target_harvest_plan.json"
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=FRESH_HARVEST_PHASE,
        report=report,
        artifacts=[_artifact(report_path)],
    )
    manifest_path = output_dir / "fresh_target_harvest_plan_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "decision": decision,
        "report": report,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "selected_instances": selected_instances,
    }


# ---------------------------------------------------------------------------
# Execute mode
# ---------------------------------------------------------------------------


def run_fresh_target_harvest(
    plan_path: Path,
    runner: HarvestRunner,
    output_dir: Path,
    ack_docker: bool = False,
    ack_external_provider: bool = False,
) -> dict[str, Any]:
    """Execute baseline arm for each selected instance in the plan.

    Requires ack_docker=True AND ack_external_provider=True; raises RuntimeError otherwise.
    Runner: (instance_id: str, output_dir: Path) -> dict with keys
        {instance_id, status, patch_path, archive_dir}
    """
    if not ack_docker or not ack_external_provider:
        raise RuntimeError("execute mode requires --ack-docker and --ack-external-provider")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selected = plan.get("summary", {}).get("selected_instances", [])
    output_dir.mkdir(parents=True, exist_ok=True)

    harvest_candidates: list[dict[str, Any]] = []
    success_sentinels: list[dict[str, Any]] = []
    harvest_errors: list[dict[str, Any]] = []

    for entry in selected:
        instance_id = entry["instance_id"]
        instance_out = output_dir / instance_id
        instance_out.mkdir(parents=True, exist_ok=True)
        try:
            result = runner(instance_id, instance_out)
            status = result.get("status", "error")
            if status == "unresolved":
                harvest_candidates.append(
                    {
                        "source_task_id": instance_id,
                        "pair_id": "",
                        "selection_role": "harvest_candidate",
                        "source_family": entry.get("source_family", _source_family(instance_id)),
                        "contamination_status": "uncontaminated_harvest",
                        "patch_path": result.get("patch_path"),
                        "archive_dir": result.get("archive_dir"),
                        "baseline_status": "unresolved",
                        # Fields required by write_protocol_v2_pair_inputs_evidence
                        "candidate_static_prefix_index": None,
                        "intervention_policy_id": None,
                        "recalibrated_trigger_mode": "live_feature_signature_window",
                        "exact_static_prefix_trigger_disabled": True,
                        "selection_status": "eligible_for_live_pair",
                        "same_pair_posthoc_positive_claim_allowed": False,
                        "phase6_live_pair_authorized": False,
                        "official_eval_authorized": False,
                    }
                )
            elif status == "resolved":
                success_sentinels.append(
                    {
                        "source_task_id": instance_id,
                        "selection_role": "harvest_candidate",
                        "source_family": entry.get("source_family", _source_family(instance_id)),
                        "contamination_status": "uncontaminated_harvest",
                        "baseline_status": "resolved",
                        "patch_path": result.get("patch_path"),
                        "archive_dir": result.get("archive_dir"),
                        "note": "baseline_resolved_not_a_failure_target",
                    }
                )
            else:
                harvest_errors.append(
                    {
                        "source_task_id": instance_id,
                        "status": status,
                        "raw_result": result,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            harvest_errors.append(
                {
                    "source_task_id": instance_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    decision = (
        "fresh_target_harvest_execute_complete"
        if harvest_candidates
        else "fresh_target_harvest_execute_no_candidates"
    )
    gates = {
        "ack_docker": ack_docker,
        "ack_external_provider": ack_external_provider,
        "at_least_one_harvest_candidate": len(harvest_candidates) > 0,
        "no_unhandled_errors": len(harvest_errors) == 0,
    }
    summary = {
        "total_instances": len(selected),
        "harvest_candidates": len(harvest_candidates),
        "success_sentinels": len(success_sentinels),
        "harvest_errors": len(harvest_errors),
    }
    report = generate_report(
        phase=FRESH_HARVEST_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "claim_boundary": CLAIM_BOUNDARY,
            "summary": summary,
            "harvest_candidates": harvest_candidates,
            "success_sentinels": success_sentinels,
            "harvest_errors": harvest_errors,
        },
    )

    report_path = output_dir / "fresh_target_harvest_execute_report.json"
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=FRESH_HARVEST_PHASE,
        report=report,
        artifacts=[_artifact(report_path)],
    )
    manifest_path = output_dir / "fresh_target_harvest_execute_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "decision": decision,
        "report": report,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "harvest_candidates": harvest_candidates,
        "success_sentinels": success_sentinels,
        "harvest_errors": harvest_errors,
    }


__all__ = [
    "CLAIM_BOUNDARY",
    "FRESH_HARVEST_PHASE",
    "HarvestRunner",
    "run_fresh_target_harvest",
    "write_fresh_target_harvest_plan",
]
