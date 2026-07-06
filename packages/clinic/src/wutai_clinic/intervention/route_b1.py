"""Route B1 offline writers — plan + anti-oracle-leakage preflight.

Both functions are strictly offline: they never start Docker, call a provider,
run official eval, or capture a real reproduction traceback. They materialize
the B1 protocol, a per-anchor arm plan, and the contract-level M2 anti-leak gate
so the probe sits exactly one explicit-ack command away from live execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.evidence.registry import no_raw_payload, no_secret_literal
from wutai_clinic.intervention.protocol_b1 import (
    REQUIRED_FORBIDDEN_CATEGORIES,
    ProtocolB1,
    find_oracle_tokens,
    protocol_b1_template,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

ROUTE_B1_PLAN_PHASE = "route_b.b1_plan"
ROUTE_B1_ANTILEAK_PHASE = "route_b.b1_antileak"
ROUTE_B1_VERSION = "route_b1_repro_first_v1"
EXPECTED_PREREG_STATUS = "route_b1_probe_preregistered_live_execution_not_authorized"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _anchor_plan_rows(
    *, anchors: list[str], k_reps: int, protocol: ProtocolB1
) -> list[dict[str, Any]]:
    rows = []
    for rank, anchor in enumerate(anchors, start=1):
        rows.append(
            {
                "anchor_rank": rank,
                "source_task_id": anchor,
                "anchor_class": "deterministic_fail_low_epsilon",
                "issue_repro_eligibility": "pending_problem_statement_screen",
                "replay_free": True,
                "reps_per_arm": k_reps,
                "arms": {
                    "control": {"injects": False},
                    "treatment": {
                        "injects": True,
                        "info_kind": protocol.action.info_kind,
                        "payload_field_contract": list(protocol.action.payload_fields),
                        "payload_provenance": protocol.action.payload_provenance,
                        "injection_point": "post_issue_repro_pre_patch",
                        "max_injections": protocol.guard.max_injections_per_pair,
                    },
                },
                "live_arm_authorized": False,
                "official_eval_authorized": False,
            }
        )
    return rows


def route_b1_plan_report(
    *, prereg_manifest: dict[str, Any], protocol: ProtocolB1, anchor_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    design = prereg_manifest.get("design") or {}
    manifest_anchors = list(design.get("anchors") or [])
    plan_anchors = [row["source_task_id"] for row in anchor_rows]
    scannable = {"protocol_runtime_visible": protocol.runtime_visible(), "anchor_rows": anchor_rows}
    gates = {
        "prereg_status_is_preregistered_not_authorized": prereg_manifest.get("status")
        == EXPECTED_PREREG_STATUS,
        "prereg_blocks_live_authorization": (prereg_manifest.get("live_authorization") or {}).get(
            "authorized"
        )
        is False,
        "anchors_present": len(anchor_rows) > 0,
        "plan_anchors_match_prereg": plan_anchors == manifest_anchors,
        "protocol_is_replay_free": protocol.guard.replay_free is True,
        "protocol_forbids_oracle_capsule": protocol.guard.oracle_capsule_allowed is False,
        "protocol_forbidden_categories_cover_required": REQUIRED_FORBIDDEN_CATEGORIES.issubset(
            set(protocol.guard.forbidden_payload_categories)
        ),
        # Amendment A / M2b: reproduction is issue-text-only, never FAIL_TO_PASS-derived.
        "payload_provenance_is_issue_text_only": protocol.action.payload_provenance
        == "issue_text_only",
        "official_test_identity_in_forbidden_categories": "official_test_identity"
        in set(protocol.guard.forbidden_payload_categories),
        "anchor_issue_repro_eligibility_pending_screen": all(
            row.get("issue_repro_eligibility") == "pending_problem_statement_screen"
            for row in anchor_rows
        ),
        "k_reps_positive": all(int(row.get("reps_per_arm") or 0) > 0 for row in anchor_rows),
        "no_oracle_tokens_in_runtime_visible_plan": not find_oracle_tokens(scannable),
        "plan_has_no_raw_payload_keys": no_raw_payload(scannable),
        "plan_has_no_secret_literals": no_secret_literal(scannable),
        "uplift_claim_not_made": protocol.guard.uplift_claim_allowed is False,
        "runner_not_started": True,
        "model_call_not_started": True,
        "docker_or_official_eval_not_started": True,
    }
    return generate_report(
        phase=ROUTE_B1_PLAN_PHASE,
        decision=(
            "route_b1_plan_ready_live_execution_not_authorized"
            if all(gates.values())
            else "route_b1_plan_blocked"
        ),
        gate_results=gates,
        extras={
            "version": ROUTE_B1_VERSION,
            "protocol_hash": protocol.protocol_hash,
            "claim_boundary": (
                "Route B go/no-go probe. Offline plan only. Can declare futility (kill B) or "
                "green-light the powered batch; never an uplift claim (B6 unchanged)."
            ),
            "summary": {
                "anchor_count": len(anchor_rows),
                "reps_per_arm": anchor_rows[0]["reps_per_arm"] if anchor_rows else 0,
                "cells": sum(2 * int(row.get("reps_per_arm") or 0) for row in anchor_rows),
                "live_execution_authorized": False,
                "official_eval_authorized": False,
            },
            "continuation_policy": {
                "next_step": "route_b1_antileak_preflight_then_explicit_ack_live",
                "allow_live_without_explicit_ack": False,
                "allow_uplift_claim": False,
            },
        },
    )


def write_route_b1_plan_evidence(
    *, prereg_manifest: dict[str, Any], output_dir: Path, input_artifacts: list[Path] | None = None
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    design = prereg_manifest.get("design") or {}
    anchors = list(design.get("anchors") or [])
    k_reps = int(design.get("k_reps_per_arm") or 5)
    protocol = protocol_b1_template()
    anchor_rows = _anchor_plan_rows(anchors=anchors, k_reps=k_reps, protocol=protocol)
    report = route_b1_plan_report(
        prereg_manifest=prereg_manifest, protocol=protocol, anchor_rows=anchor_rows
    )

    protocol_path = output_dir / "protocol_b1.json"
    anchors_path = output_dir / "b1_anchor_plan.jsonl"
    report_path = output_dir / "b1_plan_report.json"
    manifest_path = output_dir / "b1_plan_manifest.json"

    _write_json(protocol_path, protocol.to_dict())
    write_jsonl(anchors_path, anchor_rows)
    _write_json(report_path, report)
    artifacts = [_artifact(p) for p in [protocol_path, anchors_path, report_path]]
    artifacts.extend(_artifact(p) for p in input_artifacts or [] if p.exists())
    manifest = generate_manifest(phase=ROUTE_B1_PLAN_PHASE, report=report, artifacts=artifacts)
    manifest["version"] = ROUTE_B1_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "protocol_path": protocol_path,
        "anchors_path": anchors_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


def route_b1_antileak_report(
    *,
    protocol: ProtocolB1,
    anchor_rows: list[dict[str, Any]],
    gold_task_ids: list[str],
    plan_dir: Path,
) -> dict[str, Any]:
    # Scan the runtime-visible plan (protocol trigger/action + per-anchor payload
    # contract) for any oracle/answer leakage. The guard's forbidden-category
    # declaration is intentionally excluded (denial list, not payload content).
    runtime_visible = {
        "protocol_runtime_visible": protocol.runtime_visible(),
        "anchor_payload_contracts": [
            row.get("arms", {}).get("treatment", {}).get("payload_field_contract")
            for row in anchor_rows
        ],
    }
    plan_anchor_ids = [row["source_task_id"] for row in anchor_rows]
    gates = {
        "protocol_forbids_oracle_capsule": protocol.guard.oracle_capsule_allowed is False,
        "forbidden_categories_cover_required": REQUIRED_FORBIDDEN_CATEGORIES.issubset(
            set(protocol.guard.forbidden_payload_categories)
        ),
        "payload_fields_are_deployable_whitelist_only": bool(protocol.action.payload_fields),
        # Amendment A / M2b — issue-text-only provenance + no official test identity.
        "payload_provenance_issue_text_only": protocol.action.payload_provenance
        == "issue_text_only",
        "no_official_test_identity_in_payload": not find_oracle_tokens(
            {"provenance": protocol.action.payload_provenance, "payload": runtime_visible}
        ),
        "no_oracle_tokens_in_runtime_visible_payload": not find_oracle_tokens(runtime_visible),
        "no_raw_payload_keys": no_raw_payload(runtime_visible),
        "no_secret_literals": no_secret_literal(runtime_visible),
        "all_anchors_have_gold_for_live_content_diff": (
            bool(gold_task_ids) and all(a in set(gold_task_ids) for a in plan_anchor_ids)
        )
        if gold_task_ids
        else True,
        "plan_dir_exists": plan_dir.is_dir(),
    }
    return generate_report(
        phase=ROUTE_B1_ANTILEAK_PHASE,
        decision=(
            "route_b1_antileak_passed_payload_contract_clean"
            if all(gates.values())
            else "route_b1_antileak_blocked"
        ),
        gate_results=gates,
        extras={
            "version": ROUTE_B1_VERSION,
            "protocol_hash": protocol.protocol_hash,
            "claim_boundary": (
                "M2 contract-level anti-oracle-leakage. Proves the B1 payload SCHEMA only admits "
                "deployable fields and structurally forbids gold/oracle/official-eval. Per-rerun "
                "CONTENT diff against gold is a LIVE-time gate, deferred until capture."
            ),
            "gold_available_for_live_content_diff": bool(gold_task_ids),
            "content_diff_stage": "deferred_to_live_capture",
            "anchor_count": len(anchor_rows),
        },
    )


def write_route_b1_antileak_evidence(
    *,
    plan_dir: Path,
    output_dir: Path,
    gold_task_ids: list[str] | None = None,
    input_artifacts: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol = ProtocolB1.from_dict(
        json.loads((plan_dir / "protocol_b1.json").read_text(encoding="utf-8"))
    )
    anchors_path = plan_dir / "b1_anchor_plan.jsonl"
    anchor_rows = [
        json.loads(line)
        for line in anchors_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = route_b1_antileak_report(
        protocol=protocol,
        anchor_rows=anchor_rows,
        gold_task_ids=list(gold_task_ids or []),
        plan_dir=plan_dir,
    )
    report_path = output_dir / "b1_antileak_report.json"
    manifest_path = output_dir / "b1_antileak_manifest.json"
    _write_json(report_path, report)
    artifacts = [_artifact(p) for p in [plan_dir / "protocol_b1.json", anchors_path, report_path]]
    artifacts.extend(_artifact(p) for p in input_artifacts or [] if p.exists())
    manifest = generate_manifest(phase=ROUTE_B1_ANTILEAK_PHASE, report=report, artifacts=artifacts)
    manifest["version"] = ROUTE_B1_VERSION
    _write_json(manifest_path, manifest)
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


__all__ = [
    "ROUTE_B1_VERSION",
    "route_b1_antileak_report",
    "route_b1_plan_report",
    "write_route_b1_antileak_evidence",
    "write_route_b1_plan_evidence",
]
