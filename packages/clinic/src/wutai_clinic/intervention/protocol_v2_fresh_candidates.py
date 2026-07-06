from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V2_FRESH_CANDIDATE_PHASE = "6.protocol_v2_fresh_candidate_gate"
PROTOCOL_V2_FRESH_CANDIDATE_VERSION = "phase6_protocol_v2_fresh_candidate_gate_v1"
EXPECTED_DRY_RUN_DECISION = "protocol_v2_dry_run_gate_passed_live_execution_not_authorized"
ALLOWED_REPLAY_RISK_LEVELS = {
    "no_known_replay_nondeterminism_patterns",
    "low_replay_nondeterminism_risk",
}
OFFICIAL_COMPLETION_PATTERNS = (
    "*dual_scorecard.json",
    "*official_eval_pair_summary.jsonl",
    "*sweagent_official_pair_summary.jsonl",
    "*batch_outcomes_pairs.jsonl",
    "*batch_stability_pairs.jsonl",
    "*cumulative_pair_diagnosis.jsonl",
)
SUPPORTED_INTERVENTION_POLICIES = {
    "break_recurrence_and_replan",
    "error_observation_recovery",
    "insert_validation_checkpoint",
    "same_action_escape",
}
MERGE_KEYS = {
    "candidate_diagnostic_score",
    "candidate_prefix_index",
    "candidate_reason_codes",
    "candidate_static_prefix_index",
    "exact_static_prefix_trigger_disabled",
    "fresh_rank",
    "intervention_policy_id",
    "next_batch_rank",
    "pair_id",
    "recalibrated_trigger_mode",
    "replay_risk_level",
    "selection_index",
    "selection_role",
    "selection_status",
    "source_family",
    "source_task_id",
}
BOUNDARY = (
    "Protocol v2 fresh-candidate gate only. It selects unused failure targets with "
    "materialized replay/config inputs, excludes completed official-eval tasks and "
    "known replay-nondeterminism state mismatches, and does not authorize live execution."
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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


def _rank_key(row: dict[str, Any], index: int) -> tuple[int, int, str]:
    for key in ("next_batch_rank", "fresh_rank", "selection_index", "execution_index"):
        value = row.get(key)
        try:
            return (int(value), index, str(row.get("pair_id") or ""))
        except (TypeError, ValueError):
            continue
    return (10_000 + index, index, str(row.get("pair_id") or ""))


def _iter_records(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix == ".jsonl":
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        payload = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    return [payload] if isinstance(payload, dict) else []


def _official_completed(root: Path) -> set[tuple[str, str]]:
    completed = set()
    seen_paths: set[Path] = set()
    for pattern in OFFICIAL_COMPLETION_PATTERNS:
        for path in root.rglob(pattern):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            for payload in _iter_records(path):
                if payload.get("official_eval_completed") is not True:
                    continue
                pair_id = str(payload.get("pair_id") or "")
                task_id = str(payload.get("source_task_id") or "")
                if pair_id or task_id:
                    completed.add((pair_id, task_id))
    return completed


def _known_replay_mismatch(root: Path) -> set[tuple[str, str]]:
    blocked = set()
    for path in root.rglob("*state_capsule_mismatch_audit_report.json"):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        decision = str(payload.get("decision") or "")
        likely_root = str(payload.get("likely_root_cause") or "")
        if "nondeterministic" not in decision and "nondeterministic" not in likely_root:
            continue
        pair_id = str(payload.get("pair_id") or "")
        task_id = str(payload.get("source_task_id") or "")
        if pair_id or task_id:
            blocked.add((pair_id, task_id))
    return blocked


def _is_used(
    row: dict[str, Any],
    *,
    used_pairs: set[tuple[str, str]],
) -> bool:
    pair_id = str(row.get("pair_id") or "")
    task_id = str(row.get("source_task_id") or "")
    used_pair_ids = {used_pair_id for used_pair_id, _task_id in used_pairs if used_pair_id}
    used_task_ids = {used_task_id for _pair_id, used_task_id in used_pairs if used_task_id}
    return (pair_id, task_id) in used_pairs or pair_id in used_pair_ids or task_id in used_task_ids


def _identity(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("pair_id") or ""), str(row.get("source_task_id") or ""))


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _candidate_quality(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    policy = str(row.get("intervention_policy_id") or "")
    prefix = row.get("candidate_static_prefix_index", row.get("candidate_prefix_index"))
    return (
        1 if row.get("selection_role") == "failure_target" else 0,
        1 if policy in SUPPORTED_INTERVENTION_POLICIES else 0,
        1 if _has_value(prefix) else 0,
        1 if row.get("recalibrated_trigger_mode") == "live_feature_signature_window" else 0,
        1 if row.get("selection_status") == "eligible_for_live_pair" else 0,
    )


def _coalesced_rows(
    candidate_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(candidate_rows):
        grouped.setdefault(_identity(row), []).append((index, row))

    coalesced: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    for identity, rows in grouped.items():
        if not identity[0] and not identity[1]:
            coalesced.extend(dict(row) for _index, row in rows)
            continue
        best_index, best_row = max(rows, key=lambda item: (_candidate_quality(item[1]), -item[0]))
        merged = {
            key: value for key, value in best_row.items() if key in MERGE_KEYS and _has_value(value)
        }
        for _index, row in sorted(
            rows,
            key=lambda item: (_candidate_quality(item[1]), -item[0]),
            reverse=True,
        ):
            for key in MERGE_KEYS:
                value = row.get(key)
                if key == "candidate_reason_codes" and isinstance(value, list):
                    existing = list(merged.get(key) or [])
                    for reason in value:
                        if reason not in existing:
                            existing.append(reason)
                    if existing:
                        merged[key] = existing
                    continue
                if key not in merged and _has_value(value):
                    merged[key] = value
        if "candidate_static_prefix_index" not in merged and "candidate_prefix_index" in merged:
            merged["candidate_static_prefix_index"] = merged["candidate_prefix_index"]
        coalesced.append(merged)
        duplicate_rows.extend(row for index, row in rows if index != best_index)
    return coalesced, duplicate_rows


def _materialized_inputs(
    row: dict[str, Any],
    *,
    pair_input_roots: list[Path],
) -> dict[str, Any]:
    task_id = str(row.get("source_task_id") or "")
    if not task_id:
        return {}
    for root in pair_input_roots:
        task_dir = root / task_id
        candidate_path = task_dir / f"{task_id}_candidate.jsonl"
        replay_path = task_dir / f"{task_id}_replay_actions.json"
        config_path = task_dir / f"{task_id}_run_single_config.json"
        input_report_path = task_dir / f"{task_id}_live_pair_inputs_report.json"
        if candidate_path.is_file() or replay_path.is_file() or config_path.is_file():
            return {
                "candidate_path": candidate_path,
                "replay_actions_path": replay_path,
                "run_single_config_path": config_path,
                "input_report_path": input_report_path,
            }
    return {}


def _replay_risk_level(row: dict[str, Any], materialized: dict[str, Any]) -> str:
    raw = row.get("replay_risk_level")
    if isinstance(raw, str) and raw:
        return raw
    input_report_path = materialized.get("input_report_path")
    if isinstance(input_report_path, Path) and input_report_path.is_file():
        report = _load_json(input_report_path)
        risk_level = report.get("replay_determinism_screen", {}).get("risk_level")
        if isinstance(risk_level, str) and risk_level:
            return risk_level
        if report.get("passed") is True and report.get("gates", {}).get("replay_actions_written"):
            return "missing_replay_risk_level"
    return "missing_replay_risk_level"


def _safe_candidate(
    row: dict[str, Any],
    *,
    fresh_rank: int,
    materialized: dict[str, Any],
    replay_risk_level: str,
) -> dict[str, Any]:
    return {
        "phase": PROTOCOL_V2_FRESH_CANDIDATE_PHASE,
        "protocol_version": PROTOCOL_V2_FRESH_CANDIDATE_VERSION,
        "fresh_rank": fresh_rank,
        "pair_id": row.get("pair_id"),
        "source_task_id": row.get("source_task_id"),
        "source_family": row.get("source_family"),
        "selection_role": row.get("selection_role"),
        "intervention_policy_id": row.get("intervention_policy_id"),
        "candidate_static_prefix_index": row.get(
            "candidate_static_prefix_index",
            row.get("candidate_prefix_index"),
        ),
        "candidate_diagnostic_score": row.get("candidate_diagnostic_score"),
        "candidate_reason_codes": list(row.get("candidate_reason_codes") or []),
        "replay_risk_level": replay_risk_level,
        "recalibrated_trigger_mode": row.get(
            "recalibrated_trigger_mode",
            "live_feature_signature_window",
        ),
        "exact_static_prefix_trigger_disabled": row.get(
            "exact_static_prefix_trigger_disabled",
            True,
        ),
        "protocol_v2_required": True,
        "protocol_v2_prescription_required": True,
        "same_pair_posthoc_positive_claim_allowed": False,
        "phase6_live_pair_authorized": False,
        "official_eval_authorized": False,
        "candidate_path": materialized["candidate_path"].as_posix(),
        "replay_actions_path": materialized["replay_actions_path"].as_posix(),
        "run_single_config_path": materialized["run_single_config_path"].as_posix(),
        "input_report_path": materialized["input_report_path"].as_posix()
        if materialized["input_report_path"].is_file()
        else None,
        "contamination_status": "fresh_not_seen_in_completed_official_eval",
        "claim_boundary": (
            "Fresh Protocol v2 candidate reference only; positive attribution requires "
            "a new control/treatment run plus isolated official eval."
        ),
    }


def _excluded_candidate(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "pair_id": row.get("pair_id"),
        "source_task_id": row.get("source_task_id"),
        "source_family": row.get("source_family"),
        "selection_role": row.get("selection_role"),
        "intervention_policy_id": row.get("intervention_policy_id"),
        "selection_status": row.get("selection_status"),
        "exclusion_reason": reason,
        "same_pair_posthoc_positive_claim_allowed": False,
    }


def select_protocol_v2_fresh_candidates(
    *,
    candidate_rows: list[dict[str, Any]],
    protocol_v2_dry_run_report: dict[str, Any],
    official_eval_roots: list[Path],
    pair_input_roots: list[Path],
    target_pair_count: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bool]]:
    used_pairs: set[tuple[str, str]] = set()
    known_mismatches: set[tuple[str, str]] = set()
    for root in official_eval_roots:
        used_pairs |= _official_completed(root)
        known_mismatches |= _known_replay_mismatch(root)

    fresh_rows: list[dict[str, Any]] = []
    coalesced, duplicate_rows = _coalesced_rows(candidate_rows)
    excluded_rows: list[dict[str, Any]] = [
        _excluded_candidate(row, reason="duplicate_candidate") for row in duplicate_rows
    ]
    sorted_rows = [
        row
        for _key, row in sorted((_rank_key(row, index), row) for index, row in enumerate(coalesced))
    ]
    for row in sorted_rows:
        if row.get("selection_role") != "failure_target":
            excluded_rows.append(_excluded_candidate(row, reason="not_failure_target"))
            continue
        if row.get("selection_status") not in {None, "eligible_for_live_pair"}:
            excluded_rows.append(_excluded_candidate(row, reason="selection_status_not_eligible"))
            continue
        if _is_used(row, used_pairs=used_pairs):
            excluded_rows.append(
                _excluded_candidate(row, reason="official_eval_completed_contaminated")
            )
            continue
        if _is_used(row, used_pairs=known_mismatches):
            excluded_rows.append(
                _excluded_candidate(row, reason="known_replay_nondeterminism_or_state_mismatch")
            )
            continue
        materialized = _materialized_inputs(row, pair_input_roots=pair_input_roots)
        if not materialized:
            excluded_rows.append(
                _excluded_candidate(row, reason="materialized_pair_inputs_missing")
            )
            continue
        if not materialized["replay_actions_path"].is_file():
            excluded_rows.append(_excluded_candidate(row, reason="replay_actions_missing"))
            continue
        if not materialized["run_single_config_path"].is_file():
            excluded_rows.append(_excluded_candidate(row, reason="run_single_config_missing"))
            continue
        replay_risk_level = _replay_risk_level(row, materialized)
        if replay_risk_level not in ALLOWED_REPLAY_RISK_LEVELS:
            excluded_rows.append(_excluded_candidate(row, reason="replay_risk_not_allowed"))
            continue
        fresh_rows.append(
            _safe_candidate(
                row,
                fresh_rank=len(fresh_rows) + 1,
                materialized=materialized,
                replay_risk_level=replay_risk_level,
            )
        )

    dry_run_ready = protocol_v2_dry_run_report.get("decision") == EXPECTED_DRY_RUN_DECISION
    full_batch_ready = len(fresh_rows) >= target_pair_count
    gates = {
        "protocol_v2_dry_run_ready": dry_run_ready,
        "candidate_rows_present": len(candidate_rows) > 0,
        "at_least_one_fresh_failure_target": len(fresh_rows) > 0,
        "target_pair_count_is_positive": target_pair_count > 0,
        "fresh_candidates_are_failure_targets": all(
            row.get("selection_role") == "failure_target" for row in fresh_rows
        ),
        "fresh_candidates_not_completed_official_eval": all(
            not _is_used(row, used_pairs=used_pairs) for row in fresh_rows
        ),
        "fresh_candidates_no_known_replay_mismatch": all(
            not _is_used(row, used_pairs=known_mismatches) for row in fresh_rows
        ),
        "fresh_candidates_have_replay_actions": all(
            Path(str(row.get("replay_actions_path") or "")).is_file() for row in fresh_rows
        ),
        "fresh_candidates_have_run_single_config": all(
            Path(str(row.get("run_single_config_path") or "")).is_file() for row in fresh_rows
        ),
        "fresh_candidates_do_not_authorize_execution": all(
            row.get("phase6_live_pair_authorized") is False
            and row.get("official_eval_authorized") is False
            for row in fresh_rows
        ),
        "candidate_payload_has_no_raw_payload_keys": no_raw_payload(
            {"fresh_rows": fresh_rows, "excluded_rows": excluded_rows}
        ),
        "candidate_payload_has_no_secret_literals": no_secret_literal(
            {"fresh_rows": fresh_rows, "excluded_rows": excluded_rows}
        ),
    }
    gates["full_target_pair_count_met"] = full_batch_ready
    return fresh_rows, excluded_rows, gates


def protocol_v2_fresh_candidate_report(
    *,
    candidate_rows: list[dict[str, Any]],
    protocol_v2_dry_run_report: dict[str, Any],
    official_eval_roots: list[Path],
    pair_input_roots: list[Path],
    target_pair_count: int = 4,
) -> dict[str, Any]:
    fresh_rows, excluded_rows, gates = select_protocol_v2_fresh_candidates(
        candidate_rows=candidate_rows,
        protocol_v2_dry_run_report=protocol_v2_dry_run_report,
        official_eval_roots=official_eval_roots,
        pair_input_roots=pair_input_roots,
        target_pair_count=target_pair_count,
    )
    role_counts = Counter(str(row.get("selection_role")) for row in fresh_rows)
    policy_counts = Counter(str(row.get("intervention_policy_id")) for row in fresh_rows)
    exclusion_counts = Counter(str(row.get("exclusion_reason")) for row in excluded_rows)
    if not fresh_rows:
        decision = "protocol_v2_fresh_candidate_set_blocked_no_fresh_failure_targets"
    elif len(fresh_rows) >= target_pair_count:
        decision = "protocol_v2_fresh_candidate_set_ready_for_planned_preflight"
    else:
        decision = "protocol_v2_fresh_candidate_set_ready_limited_underpowered_no_batch_claim"
    summary = {
        "candidate_row_count": len(candidate_rows),
        "fresh_candidate_count": len(fresh_rows),
        "excluded_candidate_count": len(excluded_rows),
        "fresh_failure_target_count": role_counts.get("failure_target", 0),
        "target_pair_count": target_pair_count,
        "full_batch_ready": len(fresh_rows) >= target_pair_count,
        "role_counts": dict(sorted(role_counts.items())),
        "policy_counts": dict(sorted(policy_counts.items())),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
    }
    report = generate_report(
        phase=PROTOCOL_V2_FRESH_CANDIDATE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": PROTOCOL_V2_FRESH_CANDIDATE_VERSION,
            "claim_boundary": BOUNDARY,
            "summary": summary,
            "fresh_candidates": [
                {
                    "fresh_rank": row["fresh_rank"],
                    "pair_id": row.get("pair_id"),
                    "source_task_id": row.get("source_task_id"),
                    "intervention_policy_id": row.get("intervention_policy_id"),
                    "replay_risk_level": row.get("replay_risk_level"),
                }
                for row in fresh_rows
            ],
            "continuation_policy": {
                "allow_protocol_v2_planned_preflight": bool(fresh_rows)
                and gates["protocol_v2_dry_run_ready"],
                "allow_protocol_v2_real_run": False,
                "allow_same_pair_positive_claim": False,
                "allow_official_eval_identifier_runtime_injection": False,
                "recommended_next_step": (
                    "run_protocol_v2_planned_preflight_on_first_fresh_failure_target"
                    if fresh_rows
                    else "collect_more_uncontaminated_failure_targets"
                ),
            },
        },
    )
    report["passed"] = bool(fresh_rows) and not [
        name
        for name, passed in gates.items()
        if not passed and name not in {"full_target_pair_count_met"}
    ]
    if not fresh_rows:
        report["blocking_failures"] = [
            *report.get("blocking_failures", []),
            "fresh_failure_targets_missing",
        ]
    return report


def write_protocol_v2_fresh_candidate_evidence(
    *,
    candidate_rows: list[dict[str, Any]],
    protocol_v2_dry_run_report: dict[str, Any],
    official_eval_roots: list[Path],
    pair_input_roots: list[Path],
    output_dir: Path,
    input_artifacts: list[Path] | None = None,
    target_pair_count: int = 4,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fresh_rows, excluded_rows, _gates = select_protocol_v2_fresh_candidates(
        candidate_rows=candidate_rows,
        protocol_v2_dry_run_report=protocol_v2_dry_run_report,
        official_eval_roots=official_eval_roots,
        pair_input_roots=pair_input_roots,
        target_pair_count=target_pair_count,
    )
    report = protocol_v2_fresh_candidate_report(
        candidate_rows=candidate_rows,
        protocol_v2_dry_run_report=protocol_v2_dry_run_report,
        official_eval_roots=official_eval_roots,
        pair_input_roots=pair_input_roots,
        target_pair_count=target_pair_count,
    )
    fresh_path = output_dir / "protocol_v2_fresh_candidate_set_candidates.jsonl"
    excluded_path = output_dir / "protocol_v2_fresh_candidate_set_excluded.jsonl"
    report_path = output_dir / "protocol_v2_fresh_candidate_set_report.json"
    summary_path = output_dir / "protocol_v2_fresh_candidate_set_summary.json"
    manifest_path = output_dir / "protocol_v2_fresh_candidate_set_manifest.json"

    write_jsonl(fresh_path, fresh_rows)
    write_jsonl(excluded_path, excluded_rows)
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
    artifacts = [_artifact(path) for path in [fresh_path, excluded_path, report_path, summary_path]]
    artifacts.extend(_artifact(path) for path in input_artifacts or [] if path.exists())
    manifest = generate_manifest(
        phase=PROTOCOL_V2_FRESH_CANDIDATE_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V2_FRESH_CANDIDATE_VERSION
    _write_json(manifest_path, manifest)
    return {
        "fresh_candidates": fresh_rows,
        "excluded_candidates": excluded_rows,
        "report": report,
        "manifest": manifest,
        "fresh_path": fresh_path,
        "excluded_path": excluded_path,
        "report_path": report_path,
        "summary_path": summary_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "PROTOCOL_V2_FRESH_CANDIDATE_VERSION",
    "protocol_v2_fresh_candidate_report",
    "select_protocol_v2_fresh_candidates",
    "write_protocol_v2_fresh_candidate_evidence",
]
