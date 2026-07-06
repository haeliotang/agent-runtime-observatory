"""Evidence inventory scanner: build a machine-readable index of pair-level artifacts.

This module provides read-only inventory of existing evidence artifacts and their
lineage. It asserts file-level consistency only; it makes no uplift, predictive,
or causal claims on behalf of any indexed artifact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CLAIM_BOUNDARY = (
    "This index is a read-only inventory of existing evidence artifacts and their lineage. "
    "It asserts file-level consistency only; it makes no uplift, predictive, or causal claims "
    "on behalf of any indexed artifact."
)

# ---------------------------------------------------------------------------
# Manifest helper functions (extracted from cli.py so audit command can import them)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_sha_entries(data: dict) -> list[tuple[str, str, dict]]:
    entries = []
    for section in ["artifacts", "inputs", "outputs"]:
        section_entries = data.get(section)
        if isinstance(section_entries, list):
            for index, metadata in enumerate(section_entries):
                if not isinstance(metadata, dict) or not metadata.get("sha256"):
                    continue
                raw_path = str(metadata.get("path") or f"{section}[{index}]")
                entries.append((section, raw_path, metadata))
            continue
        if not isinstance(section_entries, dict):
            continue
        for label, metadata in section_entries.items():
            if not isinstance(metadata, dict) or not metadata.get("sha256"):
                continue
            raw_path = str(metadata.get("path") or label)
            entries.append((section, raw_path, metadata))
    return entries


def _resolve_artifact_path(evidence_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    base = evidence_dir.resolve()
    candidates = [
        Path.cwd() / path,
        base / path,
        base.parent / path,
        base.parent.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _count_jsonl(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _audit_manifest_hashes(path: Path, evidence_dir: Path, data: dict) -> dict[str, object]:
    entries = []
    checked = 0
    missing = 0
    mismatched = 0
    record_checked = 0
    record_mismatched = 0
    for section, raw_path, metadata in _manifest_sha_entries(data):
        resolved = _resolve_artifact_path(evidence_dir, raw_path)
        expected_sha = str(metadata["sha256"])
        entry: dict[str, Any] = {
            "section": section,
            "path": raw_path,
            "exists": resolved.exists(),
            "sha256_match": None,
            "record_count_match": None,
        }
        if not resolved.exists():
            missing += 1
            entries.append(entry)
            continue
        checked += 1
        actual_sha = _sha256_file(resolved)
        entry["sha256_match"] = actual_sha == expected_sha
        if actual_sha != expected_sha:
            mismatched += 1
        expected_count = metadata.get("record_count", metadata.get("line_count"))
        if expected_count is not None and resolved.suffix == ".jsonl":
            record_checked += 1
            actual_count = _count_jsonl(resolved)
            entry["record_count_match"] = int(expected_count) == actual_count
            if int(expected_count) != actual_count:
                record_mismatched += 1
        entries.append(entry)
    return {
        "path": path.name,
        "hash_checked": checked,
        "hash_missing_count": missing,
        "hash_mismatch_count": mismatched,
        "record_count_checked": record_checked,
        "record_count_mismatch_count": record_mismatched,
        "entries": entries[:20],
    }


# ---------------------------------------------------------------------------
# Dataclass and status literals
# ---------------------------------------------------------------------------

# status values
STATUS_OFFICIAL_EVAL_COMPLETED = "official_eval_completed"
STATUS_MATERIALIZED_NOT_EXECUTED = "materialized_not_executed"
STATUS_PLANNED_ONLY = "planned_only"
STATUS_UNPARSED = "unparsed"

# filename patterns that identify pair-level eval artifacts
REPORT_PATTERNS = (
    "official_eval_report",
    "dual_scorecard",
    "live_pair_inputs_report",
    "pair_summary",
    "_official_eval_pair_summary",
    "four_pair_official_eval_summary",
)


@dataclass
class EvidenceIndexRow:
    instance_id: str
    pair_id: str
    protocol_stratum: str  # v0_reference | v1_fresh | v2_strict_fresh | v2_reference | v2_oracle_probe
    effect_label: str
    trajectory_class: str
    status: str  # official_eval_completed | materialized_not_executed | planned_only | unparsed
    decision: str
    lineage_note: str
    report_path: str
    manifest_path: str
    manifest_ok: bool | None
    generated_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_json_safe(path: Path) -> dict | None:
    """Return parsed JSON dict or None on any error."""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _find_manifest(report_path: Path) -> Path | None:
    """Look for a sibling manifest in the same directory."""
    parent = report_path.parent
    # common naming: replace *_report.json → *_manifest.json, or look by glob
    stem = report_path.stem
    if stem.endswith("_report"):
        candidate = parent / (stem[: -len("_report")] + "_manifest.json")
        if candidate.exists():
            return candidate
    # fall back to any *_manifest.json in the same dir
    manifests = list(parent.glob("*_manifest.json"))
    return manifests[0] if manifests else None


def _check_manifest_ok(manifest_path: Path) -> bool | None:
    """Return True if all SHA256 checks pass, False if any fail, None if no entries."""
    data = _load_json_safe(manifest_path)
    if data is None:
        return None
    result = _audit_manifest_hashes(manifest_path, manifest_path.parent, data)
    checked = int(result.get("hash_checked", 0))
    if checked == 0:
        return None
    return (
        int(result.get("hash_mismatch_count", 0)) == 0
        and int(result.get("hash_missing_count", 0)) == 0
    )


def _trajectory_class_from_report(data: dict) -> str:
    """Best-effort: check explicit fields, then infer from effect label."""
    for key in ("trajectory_class", "pair_trajectory_class", "trajectory_divergence"):
        val = data.get(key)
        if val:
            return str(val)
    effect = str(data.get("effect_label") or "")
    if "no_uplift" in effect:
        return "trajectory_diverged_no_uplift" if "both_unresolved" in effect else "no_uplift"
    return ""


# ---------------------------------------------------------------------------
# Stratification logic
# ---------------------------------------------------------------------------

# v0_reference instance IDs (from four_pair_official_eval_summary.json)
_V0_INSTANCE_IDS = {
    "astropy__astropy-7746",
    "matplotlib__matplotlib-24970",
    "pytest-dev__pytest-8365",
    "scikit-learn__scikit-learn-10949",
}


def _classify_stratum(
    instance_id: str,
    report_path: Path,
    fresh_gate_ids: set[str] | None,
) -> tuple[str, str]:
    """Return (stratum, lineage_note).

    Strata:
      v0_reference   — top-level per-instance dirs in root (4 known instances)
      v1_fresh       — under protocol_v1_fresh_official_eval*/
      v2_strict_fresh — v2 AND in fresh gate list
      v2_reference   — v2 NOT in fresh gate list
    """
    path_str = report_path.as_posix()

    # Oracle-probe layer is contaminated by design: always stratified apart,
    # never counted with fresh/reference evidence.
    if "protocol_v2_oracle_probe" in path_str:
        return "v2_oracle_probe", "contaminated_by_design"

    if instance_id in _V0_INSTANCE_IDS:
        # Check path also matches top-level pattern (direct child of root/<instance>/)
        # The report path should have the instance dir directly under the evidence root
        parts = report_path.parts
        # Find depth: root/instance_id/report.json
        for i, part in enumerate(parts):
            if part == instance_id and i == len(parts) - 2:
                return "v0_reference", ""
        # Could be nested under v0 folder but same instance — still v0 if not under protocol dirs
        if "/protocol_v1_fresh" not in path_str and "/protocol_v2" not in path_str:
            return "v0_reference", ""

    if "protocol_v1_fresh_official_eval" in path_str:
        return "v1_fresh", ""

    if "protocol_v2_official_eval" in path_str or "protocol_v2_live_pair" in path_str:
        if fresh_gate_ids is None:
            note = "fresh_gate_listing_unavailable"
            return "v2_strict_fresh", note  # can't distinguish; note it
        if instance_id in fresh_gate_ids:
            return "v2_strict_fresh", ""
        return "v2_reference", ""

    return "planned_only", ""


# ---------------------------------------------------------------------------
# Materialized-not-executed detection
# ---------------------------------------------------------------------------

_MATERIALIZED_NOT_EXECUTED_DIR_NAMES = {
    "protocol_v2_fresh_state_capsule_pair_inputs",
    "protocol_v1_fresh_state_capsule_pair_inputs",
}


def _instance_id_from_path(path: Path) -> str:
    """Best-effort extraction of SWE-bench instance id from a path."""
    # instance id looks like: org__project-NNNN
    for part in reversed(path.parts):
        if "__" in part and "-" in part:
            return part
    return path.parent.name


def _is_materialized_dir(dirpath: Path) -> bool:
    return dirpath.name in _MATERIALIZED_NOT_EXECUTED_DIR_NAMES


def _scan_materialized_not_executed(root: Path) -> list[EvidenceIndexRow]:
    """Return rows for materialized-but-not-executed pair inputs."""
    rows: list[EvidenceIndexRow] = []
    for mat_dir in root.rglob("*"):
        if not mat_dir.is_dir() or not _is_materialized_dir(mat_dir):
            continue
        # Each immediate child directory is one instance
        for instance_dir in sorted(mat_dir.iterdir()):
            if not instance_dir.is_dir():
                continue
            instance_id = instance_dir.name
            if "__" not in instance_id:
                continue
            # Find a live_pair_inputs_report.json in the dir
            reports = list(instance_dir.glob("*live_pair_inputs_report*.json"))
            report_file = reports[0] if reports else None
            data: dict = {}
            if report_file:
                data = _load_json_safe(report_file) or {}
            pair_id = str(data.get("pair_id") or "")
            rows.append(
                EvidenceIndexRow(
                    instance_id=instance_id,
                    pair_id=pair_id,
                    protocol_stratum="v2_strict_fresh",
                    effect_label="",
                    trajectory_class="",
                    status=STATUS_MATERIALIZED_NOT_EXECUTED,
                    decision="materialized_not_executed",
                    lineage_note="pair_inputs_materialized_no_official_eval_run",
                    report_path=str(report_file) if report_file else str(instance_dir),
                    manifest_path="",
                    manifest_ok=None,
                    generated_at=str(data.get("generated_at") or ""),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

_EVAL_REPORT_SUFFIXES = {
    "phase6_official_eval_report.json",
    "protocol_v1_official_eval_report.json",
    "protocol_v2_official_eval_report.json",
}

_PAIR_SUMMARY_PATTERNS = (
    "official_eval_pair_summary",
    "pair_summary",
)

_SCORECARD_PATTERNS = (
    "dual_scorecard",
    "phase6_dual_scorecard",
)

# Directories to skip entirely when scanning for pair-level eval artifacts
_SKIP_DIR_NAMES = {
    "predictions",
    "logs",
    "protocol_v2_fresh_state_capsule_pair_inputs",
    "protocol_v1_fresh_state_capsule_pair_inputs",
}


def _is_eval_report(path: Path) -> bool:
    name = path.name
    if name in _EVAL_REPORT_SUFFIXES:
        return True
    if "official_eval_report" in name and name.endswith(".json"):
        return True
    # Oracle-probe outcomes are indexed so their exclusion stays visible.
    if name == "oracle_probe_outcome_report.json":
        return True
    return False


def _load_fresh_gate_ids(root: Path) -> set[str] | None:
    """Load instance IDs from protocol_v2_fresh_candidate_set_candidates.jsonl.
    Returns None if file not found (never guess).
    """
    gate_file = (
        root
        / "protocol_v2_fresh_candidate_gate"
        / "protocol_v2_fresh_candidate_set_candidates.jsonl"
    )
    if not gate_file.exists():
        return None
    ids: set[str] = set()
    try:
        for line in gate_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                tid = row.get("source_task_id")
                if tid:
                    ids.add(str(tid))
    except Exception:
        return None
    return ids


def _scan_for_eval_report_dirs(root: Path) -> list[Path]:
    """Return list of directories that contain an official eval report."""
    result: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        # Skip files in skip dirs
        skip = False
        for part in path.parts:
            if part in _SKIP_DIR_NAMES:
                skip = True
                break
        if skip:
            continue
        if _is_eval_report(path):
            result.append(path)
    return result


def scan_evidence_root(root: Path) -> list[EvidenceIndexRow]:
    """Recursively scan root, identify pair-level artifacts, return index rows.

    - Parse failures produce an unparsed row, never raise.
    - Manifest SHA256 is validated when a sibling manifest exists.
    - Fresh gate list is loaded from protocol_v2_fresh_candidate_gate/; if missing,
      all v2 rows carry lineage_note='fresh_gate_listing_unavailable'.
    """
    rows: list[EvidenceIndexRow] = []
    seen_instance_ids: set[str] = set()  # track to avoid duplicates from eval dirs

    fresh_gate_ids = _load_fresh_gate_ids(root)
    fresh_gate_note = "" if fresh_gate_ids is not None else "fresh_gate_listing_unavailable"

    # 1. Scan eval report files
    for report_path in _scan_for_eval_report_dirs(root):
        try:
            data = _load_json_safe(report_path)
            if data is None:
                rows.append(
                    EvidenceIndexRow(
                        instance_id=_instance_id_from_path(report_path),
                        pair_id="",
                        protocol_stratum="",
                        effect_label="",
                        trajectory_class="",
                        status=STATUS_UNPARSED,
                        decision="",
                        lineage_note="parse_error",
                        report_path=str(report_path),
                        manifest_path="",
                        manifest_ok=None,
                        generated_at="",
                    )
                )
                continue

            instance_id = str(data.get("source_task_id") or "") or _instance_id_from_path(report_path)
            pair_id = str(data.get("pair_id") or "")
            effect_label = str(data.get("effect_label") or "")
            trajectory_class = _trajectory_class_from_report(data)
            decision = str(data.get("decision") or "")
            generated_at = str(data.get("generated_at") or "")

            stratum, lineage_note = _classify_stratum(instance_id, report_path, fresh_gate_ids)
            # Propagate missing gate note for v2 strata when gate file unavailable
            if not lineage_note and fresh_gate_note and stratum in ("v2_strict_fresh", "v2_reference"):
                lineage_note = fresh_gate_note

            # official_eval_completed check
            official_eval_completed = bool(data.get("official_eval_completed"))
            if official_eval_completed:
                status = STATUS_OFFICIAL_EVAL_COMPLETED
            elif stratum == "planned_only":
                status = STATUS_PLANNED_ONLY
            else:
                status = STATUS_OFFICIAL_EVAL_COMPLETED  # report exists = completed

            manifest_path_obj = _find_manifest(report_path)
            manifest_path_str = str(manifest_path_obj) if manifest_path_obj else ""
            manifest_ok: bool | None = None
            if manifest_path_obj:
                try:
                    manifest_ok = _check_manifest_ok(manifest_path_obj)
                except Exception:
                    manifest_ok = None

            # Avoid duplicate rows for same instance (e.g. report + scorecard in same dir)
            dedup_key = f"{instance_id}::{stratum}"
            if dedup_key in seen_instance_ids:
                continue
            seen_instance_ids.add(dedup_key)

            rows.append(
                EvidenceIndexRow(
                    instance_id=instance_id,
                    pair_id=pair_id,
                    protocol_stratum=stratum,
                    effect_label=effect_label,
                    trajectory_class=trajectory_class,
                    status=status,
                    decision=decision,
                    lineage_note=lineage_note,
                    report_path=str(report_path),
                    manifest_path=manifest_path_str,
                    manifest_ok=manifest_ok,
                    generated_at=generated_at,
                )
            )
        except Exception:
            rows.append(
                EvidenceIndexRow(
                    instance_id=_instance_id_from_path(report_path),
                    pair_id="",
                    protocol_stratum="",
                    effect_label="",
                    trajectory_class="",
                    status=STATUS_UNPARSED,
                    decision="",
                    lineage_note="parse_error",
                    report_path=str(report_path),
                    manifest_path="",
                    manifest_ok=None,
                    generated_at="",
                )
            )

    # 2. Scan materialized-not-executed dirs (these won't have eval reports)
    completed_ids = {r.instance_id for r in rows if r.status == STATUS_OFFICIAL_EVAL_COMPLETED}

    for mat_row in _scan_materialized_not_executed(root):
        if mat_row.instance_id not in completed_ids:
            rows.append(mat_row)

    return rows


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def build_index_summary(rows: list[EvidenceIndexRow]) -> dict[str, Any]:
    """Return per-stratum counts, label counts, uplift/harm counts, etc."""
    stratum_counts: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    uplift_pair_count = 0
    harm_pair_count = 0
    materialized_not_executed: list[str] = []
    unparsed_count = 0
    status_counts: dict[str, int] = {}

    for row in rows:
        stratum_counts[row.protocol_stratum] = stratum_counts.get(row.protocol_stratum, 0) + 1
        if row.effect_label:
            label_counts[row.effect_label] = label_counts.get(row.effect_label, 0) + 1
        if row.effect_label and "positive_uplift" in row.effect_label:
            uplift_pair_count += 1
        if row.effect_label and ("harm" in row.effect_label or "negative" in row.effect_label):
            harm_pair_count += 1
        if row.status == STATUS_MATERIALIZED_NOT_EXECUTED:
            materialized_not_executed.append(row.instance_id)
        if row.status == STATUS_UNPARSED:
            unparsed_count += 1
        status_counts[row.status] = status_counts.get(row.status, 0) + 1

    return {
        "total_rows": len(rows),
        "stratum_counts": stratum_counts,
        "label_counts": label_counts,
        "uplift_pair_count": uplift_pair_count,
        "harm_pair_count": harm_pair_count,
        "materialized_not_executed": sorted(materialized_not_executed),
        "materialized_not_executed_count": len(materialized_not_executed),
        "unparsed_count": unparsed_count,
        "status_counts": status_counts,
        "claim_boundary": CLAIM_BOUNDARY,
    }


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 string (Python 3.9+ compatible)."""
    import datetime as _dt
    # timezone.utc works in Python 3.2+
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def write_evidence_index(root: Path, output_dir: Path) -> dict[str, Any]:
    """Scan root, write index artifacts to output_dir, return paths dict."""
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = scan_evidence_root(root)
    summary = build_index_summary(rows)

    rows_path = output_dir / "evidence_index_rows.jsonl"
    report_path = output_dir / "evidence_index_report.json"
    manifest_path = output_dir / "evidence_index_manifest.json"

    # Write JSONL rows
    with rows_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")

    # Write report
    report: dict[str, Any] = {
        "claim_boundary": CLAIM_BOUNDARY,
        "decision": "evidence_index_ready",
        "generated_at": _utc_now(),
        "passed": True,
        "gates": {},
        "blocking_failures": [],
        "evidence_root": str(root),
        "output_dir": str(output_dir),
        "rows_path": str(rows_path),
        "summary": summary,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # Build manifest inline (avoids depending on io.report which may require Python 3.11+)
    rows_sha = _sha256_file(rows_path)
    report_sha = _sha256_file(report_path)
    manifest: dict[str, Any] = {
        "claim_boundary": CLAIM_BOUNDARY,
        "phase": "evidence_index",
        "generated_at": _utc_now(),
        "decision": report.get("decision", ""),
        "passed": report.get("passed"),
        "artifacts": [
            {
                "path": rows_path.as_posix(),
                "sha256": rows_sha,
                "record_count": len(rows),
            },
            {
                "path": report_path.as_posix(),
                "sha256": report_sha,
            },
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )

    return {
        "rows_path": rows_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "summary": summary,
        "report": report,
    }
