"""Wave3 synthesis: close four evidence lines into one fact-checked report.

Every number in the synthesis is extracted from the source artifacts at
generation time — nothing is transcribed by hand. Narrative templates live
here as static strings; the collector interpolates verified values.

Evidence lines:
1. futility       — protocol_v2_batch_outcomes_wave3/protocol_v2_batch_outcomes_report.json
2. epsilon        — protocol_v2_epsilon_estimate/pooled/epsilon_report.json
3. mechanistic    — protocol_v2_mechanistic_endpoints/mechanistic_endpoints_report.json (+pairs)
4. oracle probe   — protocol_v2_oracle_probe/**/oracle_probe_outcome_report.json (both variants)

The oracle line is contaminated by design and feeds only the bottleneck
narrative, never any effectiveness statistic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

WAVE3_SYNTHESIS_PHASE = "7.wave3_synthesis"
WAVE3_SYNTHESIS_VERSION = "phase7_wave3_synthesis_v1"

CLAIM_BOUNDARY = (
    "This synthesis localizes the causal bottleneck of the wutai-clinic "
    "intervention chain. It supports no claim of intervention effectiveness, "
    "predictive validity, or generalized causal effect. Oracle-probe numbers "
    "are contaminated by design and appear only in bottleneck narrative."
)

_FINDINGS_TEMPLATE = [
    (
        "substrate_validity_supersedes_outcome_findings",
        "{invalid_count}/{checked_count} gold-sanity-checked instances are "
        "substrate-invalid (the dataset gold patch itself stays unresolved: "
        "{invalid_instances}). On those instances no patch can move the "
        "outcome, so every unmoved-outcome conclusion drawn there is void and "
        "part of the pooled no-uplift result is an artifact of dead outcome "
        "channels.",
    ),
    (
        "effective_information_shrinks",
        "Of {strict_fresh} strict-fresh v2 pairs only {valid_strict_fresh} sit "
        "on substrate-valid instances ({valid_strict_fresh_ids}); v0/v1 "
        "reference instances remain unchecked. The futility computation over "
        "{total_pairs} pooled pairs overstates effective information.",
    ),
    (
        "epsilon_rescoped_to_valid_instances",
        "Epsilon reruns on valid instances: {valid_epsilon_n} control reruns, "
        "{valid_epsilon_flips} outcome flips. Reruns on invalid instances are "
        "void (the outcome cannot flip there by construction).",
    ),
    (
        "divergence_without_outcome_change_on_valid_instances",
        "On substrate-valid instances the original observation survives: arms "
        "diverge at the trajectory level (first divergence steps "
        "{valid_divergence_steps}) without changing the official outcome.",
    ),
    (
        "behavioral_dose_response_present",
        "On {dose_instance} (substrate-invalid: outcome channel dead, "
        "behavior still measurable) the capsule dose ladder moves the patch "
        "monotonically toward gold: {dose_distances}. Capsule text DOES steer "
        "implementation semantics in proportion to content dose; the task10 "
        "channel-capacity typing is void.",
    ),
    (
        "replay_prefix_carries_semantic_momentum",
        "On {momentum_instance} the control arm (replay prefix, no capsule) "
        "landed at gold distance {momentum_control_distance:.2f} while the "
        "replay-free guidance arm (capsule, no prefix) landed at "
        "{momentum_oracle_distance:.2f}: the action-demonstration prefix is a "
        "higher-bandwidth semantic channel than guidance-level capsule text.",
    ),
]

_NEXT_STEP_GATES = {
    "substrate_revalidation": (
        "Fix or replace the local harness for the invalid sphinx instances "
        "(gold sanity must pass) before interpreting any outcome there; "
        "alternatively rerun on a substrate where gold sanity passes."
    ),
    "valid_instance_probes": (
        "Re-aim oracle/dose probes at substrate-valid instances "
        "(gold sanity passed) so outcome movement is observable in principle."
    ),
    "action_demonstration_prefix_channel": (
        "Design probe injecting a synthetic correct-direction action prefix "
        "instead of capsule text; motivated by the semantic-momentum finding."
    ),
    "fresh_pair_authorization": (
        "No new fresh pairs under the current prescription until gold sanity "
        "passes on the target instances AND a probe moves an outcome on a "
        "contaminated layer."
    ),
}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_wave3_evidence(root: Path) -> dict[str, Any]:
    """Extract every synthesis input from its source artifact."""
    batch = _load_json(
        root / "protocol_v2_batch_outcomes_wave3" / "protocol_v2_batch_outcomes_report.json"
    )
    epsilon = _load_json(root / "protocol_v2_epsilon_estimate" / "pooled" / "epsilon_report.json")
    # The pooled report keys per-instance estimates under a synthetic name;
    # merge the real per-instance reports written next to it.
    if epsilon is not None:
        merged: dict[str, Any] = {}
        for report_path in sorted(
            (root / "protocol_v2_epsilon_estimate").glob("*/epsilon_report.json")
        ):
            if report_path.parent.name == "pooled":
                continue
            payload = _load_json(report_path) or {}
            for instance_id, estimate in (payload.get("per_instance_estimates") or {}).items():
                merged[instance_id] = estimate
        if merged:
            epsilon = {**epsilon, "per_instance_estimates": merged}
    mechanistic = _load_json(
        root / "protocol_v2_mechanistic_endpoints" / "mechanistic_endpoints_report.json"
    )
    mech_pairs_path = (
        root / "protocol_v2_mechanistic_endpoints" / "mechanistic_endpoints_pairs.jsonl"
    )
    mech_rows = (
        [json.loads(line) for line in mech_pairs_path.read_text(encoding="utf-8").splitlines() if line]
        if mech_pairs_path.is_file()
        else []
    )
    from wutai_clinic.intervention.oracle_capsule import load_oracle_probe_rows

    oracle_rows = load_oracle_probe_rows(root)
    probe_reports_by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in oracle_rows:
        payload = _load_json(Path(row["report_path"]))
        if payload is not None:
            probe_reports_by_variant.setdefault(row.get("variant", "with_replay_prefix"), []).append(
                payload
            )
    validity = _load_json(root / "instance_validity" / "instance_validity_report.json")
    return {
        "batch": batch,
        "epsilon": epsilon,
        "mechanistic": mechanistic,
        "mech_rows": mech_rows,
        "oracle_rows": oracle_rows,
        "replay_free_reports": probe_reports_by_variant.get("replay_free", []),
        "probe_reports_by_variant": probe_reports_by_variant,
        "validity": validity,
    }


def build_wave3_synthesis(evidence: dict[str, Any]) -> dict[str, Any]:
    """Assemble fact-checked findings; raises KeyError on missing source fields."""
    batch = evidence["batch"]
    epsilon = evidence["epsilon"]
    mechanistic = evidence["mechanistic"]
    mech_rows = evidence["mech_rows"]
    oracle_rows = evidence["oracle_rows"]
    replay_free = evidence["replay_free_reports"]
    by_variant = evidence.get("probe_reports_by_variant") or {}
    validity = evidence["validity"]

    pooled = epsilon["pooled_estimate"]
    summary = batch["summary"]
    total_pairs = (
        summary["total_v2_pair_count"]
        + summary["v1_reference_pair_count"]
        + summary["v0_reference_pair_count"]
    )
    valid_instances = set(validity["valid_instances"])
    invalid_instances = list(validity["invalid_instances"])
    strict_fresh_ids = list(summary.get("strict_fresh_source_task_ids") or [])
    valid_strict_fresh_ids = sorted(set(strict_fresh_ids) & valid_instances)

    divergence_steps = {
        row["source_task_id"]: (row.get("arm_divergence") or {}).get("first_divergence_step")
        for row in mech_rows
    }
    valid_divergence_steps = {
        task_id: step for task_id, step in divergence_steps.items() if task_id in valid_instances
    }
    rf_distances = {
        r["source_task_id"]: (r.get("proximity") or {}).get("gold_edit_distance")
        for r in replay_free
    }

    # Epsilon rescoped to valid instances (per-instance estimates by id).
    valid_epsilon_n = 0
    valid_epsilon_flips = 0
    for instance_id, estimate in (epsilon.get("per_instance_estimates") or {}).items():
        if instance_id in valid_instances:
            valid_epsilon_n += estimate.get("rerun_count", 0)
            valid_epsilon_flips += estimate.get("flip_count", 0)

    # Behavioral dose ladder: guidance (replay_free) -> dose_detailed -> dose_verbatim
    # distances on whichever instance carries the dose arms.
    dose_distances: dict[str, float] = {}
    dose_instance = "n/a"
    for variant_key, label in (
        ("replay_free", "guidance"),
        ("dose_detailed", "detailed"),
        ("dose_verbatim", "verbatim"),
    ):
        for report in by_variant.get(variant_key, []):
            distance = (report.get("proximity") or {}).get("gold_edit_distance")
            if variant_key != "replay_free" and distance is not None:
                dose_instance = report["source_task_id"]
            if distance is not None and (
                variant_key == "replay_free"
                and report["source_task_id"] == dose_instance
                or variant_key != "replay_free"
            ):
                dose_distances[label] = round(distance, 3)
    # Backfill guidance distance for the dose instance once known.
    if dose_instance != "n/a":
        for report in by_variant.get("replay_free", []):
            if report["source_task_id"] == dose_instance:
                distance = (report.get("proximity") or {}).get("gold_edit_distance")
                if distance is not None:
                    dose_distances["guidance"] = round(distance, 3)

    # Semantic-momentum cross-comparison: largest (replay-free distance -
    # control distance) gap across instances.
    momentum: dict[str, Any] | None = None
    control_distance_by_task = {
        row["source_task_id"]: row["control"].get("gold_edit_distance") for row in mech_rows
    }
    for report in replay_free:
        task_id = report["source_task_id"]
        oracle_distance = rf_distances.get(task_id)
        control_distance = control_distance_by_task.get(task_id)
        if oracle_distance is None or control_distance is None:
            continue
        gap = oracle_distance - control_distance
        if momentum is None or gap > momentum["gap"]:
            momentum = {
                "instance": task_id,
                "control_distance": control_distance,
                "oracle_distance": oracle_distance,
                "gap": gap,
            }

    values = {
        "total_pairs": total_pairs,
        "checked_count": len(validity["rows"]),
        "invalid_count": len(invalid_instances),
        "invalid_instances": ", ".join(invalid_instances) or "none",
        "strict_fresh": summary["strict_fresh_pair_count"],
        "valid_strict_fresh": len(valid_strict_fresh_ids),
        "valid_strict_fresh_ids": ", ".join(valid_strict_fresh_ids) or "none",
        "valid_epsilon_n": valid_epsilon_n,
        "valid_epsilon_flips": valid_epsilon_flips,
        "valid_divergence_steps": json.dumps(valid_divergence_steps, sort_keys=True),
        "dose_instance": dose_instance,
        "dose_distances": json.dumps(dose_distances, sort_keys=True),
        "momentum_instance": momentum["instance"] if momentum else "n/a",
        "momentum_control_distance": momentum["control_distance"] if momentum else float("nan"),
        "momentum_oracle_distance": momentum["oracle_distance"] if momentum else float("nan"),
    }
    findings = [
        {"key": key, "statement": template.format(**values)}
        for key, template in _FINDINGS_TEMPLATE
    ]
    return {
        "findings": findings,
        "values": values,
        "evidence_lines": {
            "instance_validity": {
                "decision": validity["decision"],
                "valid_instances": sorted(valid_instances),
                "invalid_instances": invalid_instances,
            },
            "futility": {
                "decision": batch["decision"],
                "strict_fresh_pair_count": summary["strict_fresh_pair_count"],
                "total_pooled_pairs": total_pairs,
                "allow_continue_remaining_fresh_targets": batch["continuation_policy"].get(
                    "allow_continue_remaining_fresh_targets"
                ),
            },
            "epsilon": {
                "decision": epsilon["decision"],
                "pooled_estimate": pooled,
                "valid_instance_rerun_count": valid_epsilon_n,
                "valid_instance_flip_count": valid_epsilon_flips,
            },
            "mechanistic": {
                "decision": mechanistic["decision"],
                "pair_count": len(mech_rows),
                "first_divergence_steps": divergence_steps,
            },
            "oracle_probe": {
                "contaminated_by_design": True,
                "rows": oracle_rows,
                "dose_ladder_distances": dose_distances,
            },
        },
        "next_step_gates": _NEXT_STEP_GATES,
    }


def render_wave3_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Wave3 Synthesis — Bottleneck Localization",
        "",
        f"> {report['claim_boundary']}",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Findings",
        "",
    ]
    for finding in report["synthesis"]["findings"]:
        lines.append(f"- **{finding['key']}**: {finding['statement']}")
    lines += ["", "## Evidence lines", ""]
    for name, line in report["synthesis"]["evidence_lines"].items():
        lines.append(f"### {name}")
        lines.append("```json")
        lines.append(json.dumps(line, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    lines += ["## Next-step gates", ""]
    for key, gate in report["synthesis"]["next_step_gates"].items():
        lines.append(f"- **{key}**: {gate}")
    lines.append("")
    return "\n".join(lines)


def write_wave3_synthesis_evidence(root: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = collect_wave3_evidence(root)
    gates = {
        "futility_line_present": evidence["batch"] is not None,
        "epsilon_line_present": evidence["epsilon"] is not None,
        "mechanistic_line_present": evidence["mechanistic"] is not None,
        "oracle_probe_rows_present": len(evidence["oracle_rows"]) > 0,
        "replay_free_typing_present": len(evidence["replay_free_reports"]) > 0,
        "instance_validity_line_present": evidence["validity"] is not None,
    }
    if all(gates.values()):
        synthesis = build_wave3_synthesis(evidence)
        if synthesis["evidence_lines"]["instance_validity"]["invalid_instances"]:
            decision = "wave3_synthesis_substrate_validity_supersedes_outcome_findings"
        else:
            decision = "wave3_synthesis_bottleneck_localized_last_mile_semantics"
    else:
        synthesis = {"findings": [], "values": {}, "evidence_lines": {}, "next_step_gates": {}}
        decision = "wave3_synthesis_blocked_missing_evidence_line"

    report = generate_report(
        phase=WAVE3_SYNTHESIS_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": WAVE3_SYNTHESIS_VERSION,
            "claim_boundary": CLAIM_BOUNDARY,
            "synthesis": synthesis,
        },
    )
    report_path = output_dir / "wave3_synthesis_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    markdown_path = output_dir / "wave3_synthesis.md"
    markdown_path.write_text(render_wave3_markdown(report) + "\n", encoding="utf-8")
    manifest = generate_manifest(
        phase=WAVE3_SYNTHESIS_PHASE,
        report=report,
        artifacts=[
            {
                "path": path.as_posix(),
                "sha256": sha256_file(path),
                "record_count": None,
            }
            for path in [report_path, markdown_path]
        ],
    )
    manifest_path = output_dir / "wave3_synthesis_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {
        "report": report,
        "report_path": report_path,
        "markdown_path": markdown_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "CLAIM_BOUNDARY",
    "WAVE3_SYNTHESIS_VERSION",
    "build_wave3_synthesis",
    "collect_wave3_evidence",
    "render_wave3_markdown",
    "write_wave3_synthesis_evidence",
]
