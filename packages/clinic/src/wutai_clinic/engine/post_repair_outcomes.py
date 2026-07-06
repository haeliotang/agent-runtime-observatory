"""Post-repair evidence rebuild: re-measured outcomes on the repaired substrate.

After the substrate repair (roman fix, 2026-06-12) every archived sphinx patch
was re-evaluated on a gold-sanity-passing harness. This module assembles those
re-measurements into the ``v2_repaired_substrate`` evidence layer:

- repaired pair rows with recomputed effect labels (re-measurement of archived
  patches — produced before measurement, no look-ahead contamination);
- an epsilon re-estimate whose reference outcome is the repaired control;
- the oracle/dose ladder outcome table (stays in the contaminated layer,
  narrative only);
- an explicit delta list against the pre-repair conclusions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.engine.epsilon import flip_rate_estimate
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

POST_REPAIR_PHASE = "8.post_repair_outcomes"
POST_REPAIR_VERSION = "phase8_post_repair_outcomes_v1"
REPAIRED_LINEAGE = "v2_repaired_substrate"

CLAIM_BOUNDARY = (
    "Re-measured outcomes on the repaired substrate. The harm-direction signal "
    "rests on two pairs, one of which sits on an instance with a ~1/3 rerun "
    "flip rate; it supports keeping the prescription frozen and redesigning "
    "the intervention toward non-interference. It supports no generalized "
    "harm or effectiveness claim."
)


def _effect_label(control_resolved: bool | None, treatment_resolved: bool | None) -> str:
    if control_resolved is None or treatment_resolved is None:
        return "pending_or_incomplete"
    if control_resolved and treatment_resolved:
        return "both_resolved_trigger_hit_pair_no_uplift"
    if not control_resolved and treatment_resolved:
        return "intervention_only_resolved_trigger_hit_candidate"
    if control_resolved and not treatment_resolved:
        return "control_only_resolved_trigger_hit_negative_candidate"
    return "both_unresolved_trigger_hit_pair_no_uplift"


def load_reeval_outcomes(root: Path) -> list[dict[str, Any]]:
    path = root / "protocol_v2_substrate_repair_reeval" / "reeval_outcomes.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def assemble_post_repair(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group re-eval rows into pairs, epsilon reruns, and probe-arm tables."""
    by_instance: dict[str, dict[str, bool | None]] = {}
    for row in rows:
        by_instance.setdefault(row["instance_id"], {})[row["arm"]] = row["resolved"]

    pair_rows = []
    epsilon_estimates: dict[str, dict[str, Any]] = {}
    probe_arms: dict[str, dict[str, bool | None]] = {}
    for instance_id, arms in sorted(by_instance.items()):
        control = arms.get("control")
        treatment = arms.get("treatment")
        if control is not None or treatment is not None:
            pair_rows.append(
                {
                    "source_task_id": instance_id,
                    "lineage": REPAIRED_LINEAGE,
                    "control_resolved": control,
                    "treatment_resolved": treatment,
                    "effect_label": _effect_label(control, treatment),
                }
            )
        rerun_outcomes = [
            arms[key] for key in sorted(arms) if key.startswith("epsilon_run_")
        ]
        if rerun_outcomes and control is not None:
            estimate = flip_rate_estimate(
                [bool(outcome) for outcome in rerun_outcomes],
                reference_outcome=bool(control),
            )
            epsilon_estimates[instance_id] = estimate
        probes = {
            key: value
            for key, value in arms.items()
            if key.startswith(("oracle_", "dose_"))
        }
        if probes:
            probe_arms[instance_id] = probes

    harm_pairs = [
        r
        for r in pair_rows
        if r["effect_label"] == "control_only_resolved_trigger_hit_negative_candidate"
    ]
    return {
        "pair_rows": pair_rows,
        "harm_pair_count": len(harm_pairs),
        "harm_pair_ids": [r["source_task_id"] for r in harm_pairs],
        "epsilon_estimates": epsilon_estimates,
        "probe_arms_excluded_from_stats": probe_arms,
    }


def build_pre_repair_delta(assembled: dict[str, Any]) -> list[dict[str, str]]:
    """Explicit list of conclusions the re-measurement overturns."""
    deltas = []
    for row in assembled["pair_rows"]:
        if row["effect_label"] == "control_only_resolved_trigger_hit_negative_candidate":
            deltas.append(
                {
                    "source_task_id": row["source_task_id"],
                    "pre_repair": "both_unresolved_trigger_hit_pair_no_uplift",
                    "post_repair": row["effect_label"],
                    "note": (
                        "Pre-repair both-unresolved was a dead-outcome-channel "
                        "artifact; re-measurement shows the control resolving and "
                        "the treatment failing (harm direction)."
                    ),
                }
            )
    for instance_id, estimate in assembled["epsilon_estimates"].items():
        if estimate["flip_count"] > 0:
            deltas.append(
                {
                    "source_task_id": instance_id,
                    "pre_repair": "epsilon_point_estimate_0_dead_channel_artifact",
                    "post_repair": (
                        f"flip rate {estimate['flip_count']}/{estimate['rerun_count']} "
                        f"(Wilson 95% upper {estimate['wilson_upper_95']:.2f})"
                    ),
                    "note": (
                        "Substrate nondeterminism is real on this instance; "
                        "single-pair attribution there is noise-dominated."
                    ),
                }
            )
    return deltas


def write_post_repair_outcomes_evidence(root: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_reeval_outcomes(root)
    validity = None
    validity_path = root / "instance_validity" / "instance_validity_report.json"
    if validity_path.is_file():
        validity = json.loads(validity_path.read_text(encoding="utf-8"))
    gates = {
        "reeval_outcomes_present": len(rows) > 0,
        "reeval_outcomes_complete": all(r.get("resolved") is not None for r in rows),
        "post_fix_validity_all_valid": bool(
            validity and not validity.get("invalid_instances")
        ),
    }
    if all(gates.values()):
        assembled = assemble_post_repair(rows)
        deltas = build_pre_repair_delta(assembled)
        decision = (
            "post_repair_outcomes_harm_direction_on_valid_substrate"
            if assembled["harm_pair_count"] > 0
            else "post_repair_outcomes_no_direction_change"
        )
    else:
        assembled = {
            "pair_rows": [],
            "harm_pair_count": 0,
            "harm_pair_ids": [],
            "epsilon_estimates": {},
            "probe_arms_excluded_from_stats": {},
        }
        deltas = []
        decision = "post_repair_outcomes_blocked_missing_inputs"

    report = generate_report(
        phase=POST_REPAIR_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": POST_REPAIR_VERSION,
            "lineage": REPAIRED_LINEAGE,
            "claim_boundary": CLAIM_BOUNDARY,
            "re_measurement_semantics": (
                "Archived patches predate the repaired measurement; re-running "
                "the official eval on them is a measurement correction, not a "
                "new execution."
            ),
            **assembled,
            "pre_repair_deltas": deltas,
            "continuation_policy": {
                "allow_generalized_harm_claim": False,
                "allow_generalized_uplift_claim": False,
                "keep_prescription_frozen": True,
                "recommended_next_step": (
                    "Redesign intervention toward non-interference; any new "
                    "arm must pass gold sanity on its target instance first."
                ),
            },
        },
    )
    report_path = output_dir / "post_repair_outcomes_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    manifest = generate_manifest(
        phase=POST_REPAIR_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": len(assembled["pair_rows"]),
            }
        ],
    )
    manifest_path = output_dir / "post_repair_outcomes_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


__all__ = [
    "CLAIM_BOUNDARY",
    "POST_REPAIR_VERSION",
    "REPAIRED_LINEAGE",
    "assemble_post_repair",
    "build_pre_repair_delta",
    "load_reeval_outcomes",
    "write_post_repair_outcomes_evidence",
]
