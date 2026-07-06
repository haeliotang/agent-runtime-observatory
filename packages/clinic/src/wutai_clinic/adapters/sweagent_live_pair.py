from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.intervention.paired_fork import default_protocol
from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    protocol_check_report,
    verify_fork_equivalence,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_LIVE_PAIR_PHASE = "5.sweagent_run_single_live_pair_outcome"
SWEAGENT_LIVE_PAIR_VERSION = "phase5_sweagent_run_single_live_pair_outcome_v1"
SWEAGENT_LIVE_PAIR_BOUNDARY = (
    "This package combines two completed sweagent-live-single arms into a pair-level "
    "outcome audit. It does not start Docker, call a provider, run official eval, or "
    "make a generalized uplift claim."
)
OutcomeSource = Literal["not_provided", "operator_supplied", "official_eval"]


@dataclass(frozen=True)
class SWEAgentLivePairSpec:
    control_dir: Path
    treatment_dir: Path
    output_dir: Path
    control_resolved: bool | None = None
    treatment_resolved: bool | None = None
    outcome_source: OutcomeSource = "not_provided"


def _json_path(root: Path, name: str) -> Path:
    return root / name


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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


def _has_outcomes(spec: SWEAgentLivePairSpec) -> bool:
    return spec.control_resolved is not None or spec.treatment_resolved is not None


def _outcomes_complete_or_absent(spec: SWEAgentLivePairSpec) -> bool:
    return (
        spec.control_resolved is None
        and spec.treatment_resolved is None
        or spec.control_resolved is not None
        and spec.treatment_resolved is not None
    )


def _capsule_hook_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == "capsule_hook"]


def _treatment_hook_equivalent(events: list[dict[str, Any]]) -> bool:
    return any(
        event.get("fork_decision") == "state_capsule_equivalent"
        and event.get("fork_passed") is True
        for event in _capsule_hook_events(events)
    )


def _raw_payload_logging_disabled(
    *,
    protocol: InterventionProtocol,
    control_capsule: StateCapsule,
    treatment_capsule: StateCapsule,
    control_events: list[dict[str, Any]],
    treatment_events: list[dict[str, Any]],
) -> bool:
    capsules_safe = (
        control_capsule.metadata.get("raw_probe_payload_logged") is False
        and treatment_capsule.metadata.get("raw_probe_payload_logged") is False
    )
    events_safe = all(
        event.get("raw_payload_logged") is False
        for event in [*control_events, *treatment_events]
        if event.get("event_type") == "capsule_hook"
    )
    return protocol.guard.raw_payload_logging is False and capsules_safe and events_safe


def _pair_summary_row(
    *,
    spec: SWEAgentLivePairSpec,
    effect_label: str,
    fork: dict[str, Any],
    control_report: dict[str, Any],
    treatment_report: dict[str, Any],
    main_attribution_eligible: bool,
) -> dict[str, Any]:
    pair_id = f"{Path(spec.control_dir).name}__{Path(spec.treatment_dir).name}"
    treatment_injected_once = int(treatment_report.get("injection_count") or 0) == 1
    return {
        "pair_id": pair_id,
        "pair_eval_scope": "main_treatment_attribution_candidate"
        if treatment_injected_once
        else "secondary_trigger_miss_audit_only",
        "control_arm_dir": spec.control_dir.as_posix(),
        "intervention_arm_dir": spec.treatment_dir.as_posix(),
        "control_resolved": spec.control_resolved,
        "intervention_resolved": spec.treatment_resolved,
        "effect_label": effect_label,
        "intervention_injected_once": treatment_injected_once,
        "main_attribution_eligible": main_attribution_eligible,
        "intervention_treatment_status": "treated_trigger_hit"
        if treatment_injected_once
        else "not_treated_trigger_miss",
        "control_capsule_fingerprint": fork["control_fingerprint"],
        "intervention_capsule_fingerprint": fork["treatment_fingerprint"],
        "capsule_mismatched_fields": list(fork["mismatched_fields"]),
        "outcome_source": spec.outcome_source,
        "single_pair_only": True,
    }


def run_sweagent_live_pair(
    *,
    spec: SWEAgentLivePairSpec,
    policy: RuntimePermissionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RuntimePermissionPolicy()
    spec.output_dir.mkdir(parents=True, exist_ok=True)

    control_paths = {
        "report": _json_path(spec.control_dir, "sweagent_live_single_report.json"),
        "capsule": _json_path(spec.control_dir, "sweagent_live_single_capsule.json"),
        "protocol": _json_path(spec.control_dir, "sweagent_live_single_protocol.json"),
        "features": _json_path(spec.control_dir, "sweagent_live_single_features.json"),
        "events": _json_path(spec.control_dir, "sweagent_live_single_events.jsonl"),
    }
    treatment_paths = {
        "report": _json_path(spec.treatment_dir, "sweagent_live_single_report.json"),
        "capsule": _json_path(spec.treatment_dir, "sweagent_live_single_capsule.json"),
        "protocol": _json_path(spec.treatment_dir, "sweagent_live_single_protocol.json"),
        "features": _json_path(spec.treatment_dir, "sweagent_live_single_features.json"),
        "events": _json_path(spec.treatment_dir, "sweagent_live_single_events.jsonl"),
    }
    required_paths = [*control_paths.values(), *treatment_paths.values()]
    required_artifacts_present = all(path.is_file() for path in required_paths)

    control_report: dict[str, Any] = {}
    treatment_report: dict[str, Any] = {}
    control_capsule: StateCapsule | None = None
    treatment_capsule: StateCapsule | None = None
    protocol = default_protocol()
    feature_windows: list[dict[str, Any]] = []
    control_events: list[dict[str, Any]] = []
    treatment_events: list[dict[str, Any]] = []
    if required_artifacts_present:
        control_report = _load_json(control_paths["report"])
        treatment_report = _load_json(treatment_paths["report"])
        control_capsule = StateCapsule.from_file(control_paths["capsule"])
        treatment_capsule = StateCapsule.from_file(treatment_paths["capsule"])
        protocol = InterventionProtocol.from_file(treatment_paths["protocol"])
        treatment_features = _load_json(treatment_paths["features"])
        feature_windows = [treatment_features] if isinstance(treatment_features, dict) else []
        control_events = _load_events(control_paths["events"])
        treatment_events = _load_events(treatment_paths["events"])

    fork = (
        verify_fork_equivalence(control_capsule, treatment_capsule)
        if control_capsule is not None and treatment_capsule is not None
        else {
            "passed": False,
            "decision": "state_capsule_missing",
            "control_fingerprint": None,
            "treatment_fingerprint": None,
            "mismatched_fields": ["missing_capsule"],
        }
    )
    protocol_check = (
        protocol_check_report(
            protocol=protocol,
            control_capsule=control_capsule,
            treatment_capsule=treatment_capsule,
            feature_windows=feature_windows,
            control_resolved=spec.control_resolved,
            treatment_resolved=spec.treatment_resolved,
        )
        if control_capsule is not None and treatment_capsule is not None
        else {"decision": "state_capsule_missing", "passed": False}
    )
    outcomes_present = _has_outcomes(spec)
    official_eval_claimed = spec.outcome_source == "official_eval"
    live_pair_structural_gates = {
        "required_live_single_artifacts_present": required_artifacts_present,
        "control_arm_completed": control_report.get("decision")
        == "sweagent_live_single_run_completed"
        and control_report.get("passed") is True,
        "treatment_arm_completed": treatment_report.get("decision")
        == "sweagent_live_single_run_completed"
        and treatment_report.get("passed") is True,
        "control_arm_type_valid": control_report.get("arm_type") == "control",
        "treatment_arm_type_valid": treatment_report.get("arm_type") == "treatment",
        "treatment_referenced_control_capsule": treatment_report.get(
            "reference_capsule_fingerprint"
        )
        == fork["control_fingerprint"],
        "state_capsule_equivalent": fork["passed"] is True,
        "treatment_hook_saw_equivalent_fork": _treatment_hook_equivalent(treatment_events),
        "treatment_injected_once": int(treatment_report.get("injection_count") or 0) == 1,
        "raw_payload_logging_disabled": (
            _raw_payload_logging_disabled(
                protocol=protocol,
                control_capsule=control_capsule,
                treatment_capsule=treatment_capsule,
                control_events=control_events,
                treatment_events=treatment_events,
            )
            if control_capsule is not None and treatment_capsule is not None
            else False
        ),
        "outcomes_complete_or_absent": _outcomes_complete_or_absent(spec),
        "outcome_source_declared_if_outcomes_present": not outcomes_present
        or spec.outcome_source != "not_provided",
        "official_eval_ack_if_claimed": not official_eval_claimed or policy.allow_official_eval,
        "official_eval_not_run_by_pair_audit": True,
        "generalized_uplift_claim_not_made": True,
    }
    structural_passed = all(live_pair_structural_gates.values())
    if not structural_passed:
        decision = "sweagent_live_pair_blocked"
    elif not outcomes_present:
        decision = "sweagent_live_pair_ready_pending_official_eval"
    else:
        decision = "sweagent_live_pair_outcome_label_ready"

    main_attribution_eligible = all(
        live_pair_structural_gates[name]
        for name in [
            "required_live_single_artifacts_present",
            "control_arm_completed",
            "treatment_arm_completed",
            "treatment_referenced_control_capsule",
            "state_capsule_equivalent",
            "treatment_hook_saw_equivalent_fork",
            "treatment_injected_once",
            "raw_payload_logging_disabled",
        ]
    )
    pair_summary = _pair_summary_row(
        spec=spec,
        effect_label=str(protocol_check["decision"]),
        fork=fork,
        control_report=control_report,
        treatment_report=treatment_report,
        main_attribution_eligible=main_attribution_eligible,
    )

    report_path = spec.output_dir / "sweagent_live_pair_report.json"
    manifest_path = spec.output_dir / "sweagent_live_pair_manifest.json"
    summary_path = spec.output_dir / "sweagent_live_pair_summary.jsonl"
    report = generate_report(
        phase=SWEAGENT_LIVE_PAIR_PHASE,
        decision=decision,
        gate_results=live_pair_structural_gates,
        extras={
            "version": SWEAGENT_LIVE_PAIR_VERSION,
            "claim_boundary": SWEAGENT_LIVE_PAIR_BOUNDARY,
            "control_dir": spec.control_dir.as_posix(),
            "treatment_dir": spec.treatment_dir.as_posix(),
            "outcome_source": spec.outcome_source,
            "control_resolved": spec.control_resolved,
            "treatment_resolved": spec.treatment_resolved,
            "effect_label": protocol_check["decision"],
            "fork_equivalence": fork,
            "protocol_check": protocol_check,
            "pair_summary_path": summary_path.as_posix(),
            "official_eval_claimed": official_eval_claimed,
            "official_eval_started": False,
        },
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(summary_path, [pair_summary])
    artifacts = [_artifact(path) for path in [*required_paths, report_path, summary_path]]
    manifest = generate_manifest(
        phase=SWEAGENT_LIVE_PAIR_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_LIVE_PAIR_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report": report,
        "manifest": manifest,
        "pair_summary": pair_summary,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "summary_path": summary_path,
    }


__all__ = [
    "SWEAGENT_LIVE_PAIR_PHASE",
    "SWEAGENT_LIVE_PAIR_VERSION",
    "SWEAgentLivePairSpec",
    "run_sweagent_live_pair",
]
