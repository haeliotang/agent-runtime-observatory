"""Route B1 cell assembler (offline) — joins live-arm M-check provenance with
official-eval resolved labels into the per-cell rows the decision engine consumes.

Pure/offline. The `resolved` label comes from the SWE-bench harness (live, STEP 6);
everything else (injection count, M2a/M2b leak status, trigger) comes from each
arm's `b1_live_arm_report.json`. This is the bridge between live results and the
preregistered §5 decision — it asserts nothing, it only reshapes evidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REP_RE = re.compile(r"rep[_-]?(\d+)")


def rep_index_from_name(name: str) -> int:
    match = _REP_RE.search(name or "")
    return int(match.group(1)) if match else 0


def cell_from_arm_report(report: dict[str, Any], *, rep: int, resolved: bool) -> dict[str, Any]:
    """Build one per-cell row from a B1 live-arm report + its resolved label."""
    arm = report.get("arm_type")
    injection_count = int(report.get("injection_count") or 0)
    leak_clean = not (report.get("m2b_leak_findings") or report.get("m2b_capture_leak_findings"))
    cell: dict[str, Any] = {
        "anchor": report.get("source_task_id"),
        "arm": arm,
        "rep": rep,
        "resolved": bool(resolved),
        "injection_count": injection_count,
        # run_exit_ok=False => SWE-agent crashed (e.g. provider error); exclude this
        # cell from the verdict so a crashed empty run is not counted as a real fail.
        "run_ok": bool(report.get("run_exit_ok", True)),
    }
    if arm == "treatment":
        # injection fires AT the trigger; injection_count==1 => trigger hit + M1 ok.
        cell["injected_once"] = injection_count == 1
        cell["leak_clean"] = leak_clean
        cell["trigger_hit"] = injection_count >= 1
    return cell


def discover_arm_reports(arms_root: Path) -> list[tuple[dict[str, Any], int]]:
    out: list[tuple[dict[str, Any], int]] = []
    for path in sorted(arms_root.rglob("b1_live_arm_report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        out.append((report, rep_index_from_name(path.parent.name)))
    return out


def resolved_map_from_labels(rows: list[dict[str, Any]]) -> dict[tuple[str, str, int], bool]:
    out: dict[tuple[str, str, int], bool] = {}
    for r in rows:
        key = (str(r.get("anchor")), str(r.get("arm")), int(r.get("rep", 0)))
        out[key] = bool(r.get("resolved"))
    return out


def assemble_cells(
    arm_reports: list[tuple[dict[str, Any], int]],
    resolved_map: dict[tuple[str, str, int], bool],
) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for report, rep in arm_reports:
        anchor = str(report.get("source_task_id"))
        arm = str(report.get("arm_type"))
        key = (anchor, arm, rep)
        if key not in resolved_map:
            incomplete.append(
                {"anchor": anchor, "arm": arm, "rep": rep, "reason": "missing_resolved_label"}
            )
            continue
        cells.append(cell_from_arm_report(report, rep=rep, resolved=resolved_map[key]))
    return {
        "phase": "route_b.b1_cells",
        "cells": cells,
        "cell_count": len(cells),
        "incomplete": incomplete,
        "incomplete_count": len(incomplete),
        "complete": not incomplete,
        "claim_boundary": "evidence reshaping only; resolved labels are the SWE-bench harness output, M-checks are the arm reports. No claim asserted.",
    }


__all__ = [
    "assemble_cells",
    "cell_from_arm_report",
    "discover_arm_reports",
    "rep_index_from_name",
    "resolved_map_from_labels",
]
