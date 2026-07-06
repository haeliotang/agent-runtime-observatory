"""Instance-level eval-substrate validity from gold-patch sanity checks.

A SWE-bench instance is VALID on this execution substrate iff the dataset's
own gold patch resolves it under the local official-eval harness. On an
invalid instance every arm is forced unresolved, so any pair on it carries
zero information for paired-outcome designs, and every "unmoved outcome"
conclusion drawn from it is void.

Gold sanity reports are produced by running the official harness with the
gold patch as the prediction; this module only collects those reports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

INSTANCE_VALIDITY_PHASE = "7.instance_validity"
INSTANCE_VALIDITY_VERSION = "phase7_instance_validity_v1"

CLAIM_BOUNDARY = (
    "Instance validity classifies the local eval substrate, not any agent or "
    "intervention. It supports no effectiveness claim; it voids, but never "
    "creates, outcome evidence."
)


def find_gold_sanity_reports(root: Path) -> dict[str, Path]:
    """Locate per-instance gold sanity report.json files under the probe layer."""
    reports: dict[str, Path] = {}
    probe_root = root / "protocol_v2_oracle_probe"
    if not probe_root.is_dir():
        return reports
    for sanity_dir in sorted(probe_root.glob("*/gold_sanity")):
        instance_id = sanity_dir.parent.name
        for report_path in sorted(sanity_dir.glob("logs/run_evaluation/*/*/*/report.json")):
            reports[instance_id] = report_path
    return reports


def classify_instance_validity(report_path: Path, instance_id: str) -> dict[str, Any] | None:
    """Read one gold sanity eval report into a validity row."""
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    instance = payload.get(instance_id)
    if not isinstance(instance, dict) or "resolved" not in instance:
        return None
    gold_resolved = bool(instance["resolved"])
    f2p = (instance.get("tests_status") or {}).get("FAIL_TO_PASS") or {}
    return {
        "instance_id": instance_id,
        "gold_resolved": gold_resolved,
        "substrate_valid": gold_resolved,
        "fail_to_pass_passed": len(f2p.get("success") or []),
        "fail_to_pass_total": len(f2p.get("success") or []) + len(f2p.get("failure") or []),
        "report_path": report_path.as_posix(),
    }


def write_instance_validity_evidence(root: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for instance_id, report_path in find_gold_sanity_reports(root).items():
        row = classify_instance_validity(report_path, instance_id)
        if row is not None:
            rows.append(row)
    valid = sorted(r["instance_id"] for r in rows if r["substrate_valid"])
    invalid = sorted(r["instance_id"] for r in rows if not r["substrate_valid"])
    gates = {
        "gold_sanity_reports_present": len(rows) > 0,
        "gold_patch_source_is_dataset": True,
    }
    if not rows:
        decision = "instance_validity_blocked_no_gold_sanity_reports"
    elif invalid:
        decision = "instance_validity_substrate_invalid_instances_found"
    else:
        decision = "instance_validity_all_checked_instances_valid"
    report = generate_report(
        phase=INSTANCE_VALIDITY_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": INSTANCE_VALIDITY_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "rows": rows,
            "valid_instances": valid,
            "invalid_instances": invalid,
            "voiding_rule": (
                "Every paired-outcome row, oracle-probe outcome decision, and "
                "epsilon rerun on an invalid instance is void: the outcome "
                "cannot move there by construction. Patch-distance (behavioral) "
                "measurements on invalid instances remain interpretable."
            ),
        },
    )
    report_path = output_dir / "instance_validity_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = generate_manifest(
        phase=INSTANCE_VALIDITY_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": len(rows),
            }
        ],
    )
    manifest_path = output_dir / "instance_validity_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


__all__ = [
    "CLAIM_BOUNDARY",
    "INSTANCE_VALIDITY_VERSION",
    "classify_instance_validity",
    "find_gold_sanity_reports",
    "write_instance_validity_evidence",
]
