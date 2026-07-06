from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

PROTOCOL_V1_FRESH_CANDIDATE_PHASE = "6.protocol_v1_fresh_candidate_gate"
PROTOCOL_V1_FRESH_CANDIDATE_VERSION = "phase6_protocol_v1_fresh_candidate_gate_v1"
EXPECTED_POOL_DECISION = "phase62_low_nondeterminism_candidate_pool_ready_with_eligible_refs"
ALLOWED_REPLAY_RISK_LEVELS = {
    "no_known_replay_nondeterminism_patterns",
    "low_replay_nondeterminism_risk",
}
BOUNDARY = (
    "Fresh-candidate gate only. This report excludes same-pair posthoc official-eval "
    "contamination and does not start Docker, call a provider, run official eval, or "
    "claim uplift."
)


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


def _used_pairs_from_plan(protocol_v1_plan: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not protocol_v1_plan:
        return set()
    return {
        (str(row.get("pair_id") or ""), str(row.get("source_task_id") or ""))
        for row in protocol_v1_plan.get("pairs") or []
        if row.get("pair_id") or row.get("source_task_id")
    }


def _used_pairs_from_diagnosis(no_uplift_diagnosis: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not no_uplift_diagnosis:
        return set()
    return {
        (str(row.get("pair_id") or ""), str(row.get("source_task_id") or ""))
        for row in no_uplift_diagnosis.get("per_pair") or []
        if row.get("pair_id") or row.get("source_task_id")
    }


def _is_used(
    row: dict[str, Any],
    *,
    used_pairs: set[tuple[str, str]],
    used_task_ids: set[str],
) -> bool:
    pair_id = str(row.get("pair_id") or "")
    task_id = str(row.get("source_task_id") or "")
    used_pair_ids = {used_pair_id for used_pair_id, _task_id in used_pairs if used_pair_id}
    return (pair_id, task_id) in used_pairs or pair_id in used_pair_ids or task_id in used_task_ids


def _rank_key(row: dict[str, Any], index: int) -> tuple[int, int, str]:
    rank = row.get("next_batch_rank")
    try:
        parsed_rank = int(rank)
    except (TypeError, ValueError):
        parsed_rank = 10_000 + index
    return (parsed_rank, index, str(row.get("pair_id") or ""))


def _fresh_candidate(row: dict[str, Any], *, fresh_rank: int) -> dict[str, Any]:
    return {
        "phase": PROTOCOL_V1_FRESH_CANDIDATE_PHASE,
        "protocol_version": PROTOCOL_V1_FRESH_CANDIDATE_VERSION,
        "fresh_rank": fresh_rank,
        "pair_id": row.get("pair_id"),
        "source_task_id": row.get("source_task_id"),
        "source_family": row.get("source_family"),
        "selection_role": row.get("selection_role"),
        "intervention_policy_id": row.get("intervention_policy_id"),
        "candidate_prefix_index": row.get("candidate_prefix_index"),
        "candidate_static_prefix_index": row.get("candidate_prefix_index"),
        "candidate_ref_sha256": row.get("candidate_ref_sha256"),
        "replay_risk_level": row.get("replay_risk_level"),
        "replay_risk_counts": row.get("replay_risk_counts", {}),
        "selection_status": row.get("selection_status"),
        "recalibrated_trigger_mode": "live_feature_signature_window",
        "exact_static_prefix_trigger_disabled": True,
        "contamination_status": "fresh_not_seen_in_protocol_v1_official_eval_diagnosis",
        "protocol_v1_required": True,
        "protocol_v1_constraint_hook_required": True,
        "state_capsule_equivalence_required": True,
        "same_pair_posthoc_positive_claim_allowed": False,
        "batch3_real_run_authorized": False,
        "phase6_live_pair_authorized": False,
        "official_eval_authorized": False,
        "claim_boundary": (
            "Fresh Protocol v1 candidate reference only; positive attribution requires a new "
            "control/treatment run plus isolated official eval."
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
        "replay_risk_level": row.get("replay_risk_level"),
        "exclusion_reason": reason,
        "same_pair_posthoc_positive_claim_allowed": False,
    }


def select_protocol_v1_fresh_candidates(
    *,
    eligible_refs: list[dict[str, Any]],
    candidate_pool_report: dict[str, Any],
    protocol_v1_plan: dict[str, Any] | None = None,
    no_uplift_diagnosis: dict[str, Any] | None = None,
    target_pair_count: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bool]]:
    used_pairs = _used_pairs_from_plan(protocol_v1_plan) | _used_pairs_from_diagnosis(
        no_uplift_diagnosis
    )
    used_task_ids = {task_id for _pair_id, task_id in used_pairs if task_id}
    fresh_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []

    sorted_refs = [
        row
        for _key, row in sorted(
            (_rank_key(row, index), row) for index, row in enumerate(eligible_refs)
        )
    ]
    for row in sorted_refs:
        if row.get("selection_status") != "eligible_for_live_pair":
            excluded_rows.append(_excluded_candidate(row, reason="selection_status_not_eligible"))
            continue
        if row.get("replay_risk_level") not in ALLOWED_REPLAY_RISK_LEVELS:
            excluded_rows.append(_excluded_candidate(row, reason="replay_risk_not_allowed"))
            continue
        if _is_used(row, used_pairs=used_pairs, used_task_ids=used_task_ids):
            excluded_rows.append(
                _excluded_candidate(row, reason="same_pair_posthoc_official_eval_contaminated")
            )
            continue
        fresh_rows.append(_fresh_candidate(row, fresh_rank=len(fresh_rows) + 1))

    pool_summary = candidate_pool_report.get("summary") or {}
    fresh_rows_are_unused = not any(
        _is_used(row, used_pairs=used_pairs, used_task_ids=used_task_ids) for row in fresh_rows
    )
    gates = {
        "candidate_pool_decision_ready": (
            candidate_pool_report.get("decision") == EXPECTED_POOL_DECISION
        ),
        "eligible_refs_present": len(eligible_refs) > 0,
        "pool_eligible_count_matches_refs": (
            int(pool_summary.get("eligible_count") or -1) == len(eligible_refs)
        ),
        "contaminated_refs_excluded": fresh_rows_are_unused,
        "fresh_refs_have_supported_risk": all(
            row.get("replay_risk_level") in ALLOWED_REPLAY_RISK_LEVELS for row in fresh_rows
        ),
        "fresh_refs_do_not_authorize_execution": all(
            row.get("phase6_live_pair_authorized") is False
            and row.get("official_eval_authorized") is False
            for row in fresh_rows
        ),
        "fresh_refs_require_protocol_v1_hook": all(
            row.get("protocol_v1_required") is True
            and row.get("protocol_v1_constraint_hook_required") is True
            for row in fresh_rows
        ),
        "fresh_refs_block_same_pair_posthoc_claims": all(
            row.get("same_pair_posthoc_positive_claim_allowed") is False
            for row in fresh_rows
        ),
        "candidate_payload_has_no_raw_payload_keys": no_raw_payload(
            {"fresh_rows": fresh_rows, "excluded_rows": excluded_rows}
        ),
        "candidate_payload_has_no_secret_literals": no_secret_literal(
            {"fresh_rows": fresh_rows, "excluded_rows": excluded_rows}
        ),
        "at_least_one_fresh_candidate": len(fresh_rows) > 0,
    }
    gates["target_pair_count_is_positive"] = target_pair_count > 0
    return fresh_rows, excluded_rows, gates


def protocol_v1_fresh_candidate_report(
    *,
    eligible_refs: list[dict[str, Any]],
    candidate_pool_report: dict[str, Any],
    protocol_v1_plan: dict[str, Any] | None = None,
    no_uplift_diagnosis: dict[str, Any] | None = None,
    target_pair_count: int = 4,
) -> dict[str, Any]:
    fresh_rows, excluded_rows, gates = select_protocol_v1_fresh_candidates(
        eligible_refs=eligible_refs,
        candidate_pool_report=candidate_pool_report,
        protocol_v1_plan=protocol_v1_plan,
        no_uplift_diagnosis=no_uplift_diagnosis,
        target_pair_count=target_pair_count,
    )
    role_counts = Counter(str(row.get("selection_role")) for row in fresh_rows)
    policy_counts = Counter(str(row.get("intervention_policy_id")) for row in fresh_rows)
    fresh_failure_target_count = role_counts.get("failure_target", 0)
    full_batch_ready = (
        len(fresh_rows) >= target_pair_count and fresh_failure_target_count >= target_pair_count
    )
    has_fresh_candidate = len(fresh_rows) > 0
    if not has_fresh_candidate:
        decision = "protocol_v1_fresh_candidate_set_blocked_no_fresh_candidates"
    elif full_batch_ready:
        decision = "protocol_v1_fresh_candidate_set_ready_for_full_batch_planned_preflight"
    else:
        decision = "protocol_v1_fresh_candidate_set_ready_limited_underpowered_no_batch_claim"

    summary = {
        "eligible_ref_count": len(eligible_refs),
        "fresh_candidate_count": len(fresh_rows),
        "excluded_candidate_count": len(excluded_rows),
        "contaminated_excluded_count": sum(
            1
            for row in excluded_rows
            if row.get("exclusion_reason") == "same_pair_posthoc_official_eval_contaminated"
        ),
        "fresh_failure_target_count": fresh_failure_target_count,
        "fresh_success_sentinel_count": role_counts.get("success_sentinel", 0),
        "target_pair_count": target_pair_count,
        "full_batch_ready": full_batch_ready,
        "role_counts": dict(sorted(role_counts.items())),
        "policy_counts": dict(sorted(policy_counts.items())),
    }
    report = generate_report(
        phase=PROTOCOL_V1_FRESH_CANDIDATE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": PROTOCOL_V1_FRESH_CANDIDATE_VERSION,
            "claim_boundary": BOUNDARY,
            "summary": summary,
            "fresh_candidates": [
                {
                    "fresh_rank": row["fresh_rank"],
                    "pair_id": row["pair_id"],
                    "source_task_id": row["source_task_id"],
                    "selection_role": row["selection_role"],
                    "intervention_policy_id": row["intervention_policy_id"],
                "candidate_prefix_index": row["candidate_prefix_index"],
                "candidate_static_prefix_index": row["candidate_static_prefix_index"],
                "replay_risk_level": row["replay_risk_level"],
            }
                for row in fresh_rows
            ],
            "excluded_candidates": excluded_rows,
            "continuation_policy": {
                "allow_protocol_v1_live_single_planned_preflight": has_fresh_candidate,
                "allow_protocol_v1_full_batch_planned_preflight": full_batch_ready,
                "allow_state_capsule_input_preparation": has_fresh_candidate,
                "allow_live_hook_preflight": has_fresh_candidate,
                "allow_batch3_real_run": False,
                "allow_phase6_live_pair_real_run": False,
                "allow_protocol_v1_real_run": False,
                "allow_official_eval": False,
                "allow_positive_uplift_claim": False,
                "recommended_next_step": (
                    "run_protocol_v1_live_single_planned_preflight_on_first_fresh_failure_target"
                    if fresh_failure_target_count > 0
                    else "collect_more_fresh_failure_target_candidates_before_real_run"
                ),
            },
        },
    )
    report["passed"] = has_fresh_candidate and not [
        name for name, passed in gates.items() if not passed and name != "at_least_one_fresh_candidate"
    ]
    if not has_fresh_candidate:
        report["passed"] = False
    report["blocking_failures"] = [name for name, passed in gates.items() if not passed]
    return report


def write_protocol_v1_fresh_candidate_evidence(
    *,
    eligible_refs: list[dict[str, Any]],
    candidate_pool_report: dict[str, Any],
    output_dir: Path,
    protocol_v1_plan: dict[str, Any] | None = None,
    no_uplift_diagnosis: dict[str, Any] | None = None,
    target_pair_count: int = 4,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fresh_rows, excluded_rows, _gates = select_protocol_v1_fresh_candidates(
        eligible_refs=eligible_refs,
        candidate_pool_report=candidate_pool_report,
        protocol_v1_plan=protocol_v1_plan,
        no_uplift_diagnosis=no_uplift_diagnosis,
        target_pair_count=target_pair_count,
    )
    report = protocol_v1_fresh_candidate_report(
        eligible_refs=eligible_refs,
        candidate_pool_report=candidate_pool_report,
        protocol_v1_plan=protocol_v1_plan,
        no_uplift_diagnosis=no_uplift_diagnosis,
        target_pair_count=target_pair_count,
    )
    fresh_path = output_dir / "protocol_v1_fresh_candidate_set_candidates.jsonl"
    excluded_path = output_dir / "protocol_v1_fresh_candidate_set_excluded.jsonl"
    report_path = output_dir / "protocol_v1_fresh_candidate_set_report.json"
    manifest_path = output_dir / "protocol_v1_fresh_candidate_set_manifest.json"
    summary_path = output_dir / "protocol_v1_fresh_candidate_set_summary.json"

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
    artifacts.extend(_artifact(path) for path in input_artifacts or [])
    manifest = generate_manifest(
        phase=PROTOCOL_V1_FRESH_CANDIDATE_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = PROTOCOL_V1_FRESH_CANDIDATE_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "fresh_candidates": fresh_rows,
        "excluded_candidates": excluded_rows,
        "fresh_path": fresh_path,
        "excluded_path": excluded_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "summary_path": summary_path,
    }


__all__ = [
    "PROTOCOL_V1_FRESH_CANDIDATE_PHASE",
    "PROTOCOL_V1_FRESH_CANDIDATE_VERSION",
    "protocol_v1_fresh_candidate_report",
    "select_protocol_v1_fresh_candidates",
    "write_protocol_v1_fresh_candidate_evidence",
]
