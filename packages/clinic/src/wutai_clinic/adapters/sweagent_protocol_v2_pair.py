from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_PROTOCOL_V2_LIVE_PAIR_PHASE = "6.protocol_v2_sweagent_live_pair"
SWEAGENT_PROTOCOL_V2_LIVE_PAIR_VERSION = "phase6_protocol_v2_sweagent_live_pair_v1"
BOUNDARY = (
    "This package combines two completed Protocol v2 SWE-agent live-single arms into a "
    "pair-level official-eval handoff. It verifies replay-prefix equivalence and patch "
    "archives, but it does not claim state-capsule equivalence, run official eval, or make "
    "a generalized uplift claim."
)

OutcomeSource = Literal["not_provided", "operator_supplied", "official_eval"]


@dataclass(frozen=True)
class SWEAgentProtocolV2LivePairSpec:
    control_dir: Path
    treatment_dir: Path
    output_dir: Path
    control_patch: Path
    treatment_patch: Path
    control_resolved: bool | None = None
    treatment_resolved: bool | None = None
    outcome_source: OutcomeSource = "not_provided"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


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


def _model_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == "model_query"]


def _constraint_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == "protocol_v2_constraint"]


def _replay_prefix(events: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    return _model_events(events)[:count]


def _replay_prefix_valid(events: list[dict[str, Any]], count: int) -> bool:
    prefix = _replay_prefix(events, count)
    return len(prefix) == count and all(
        event.get("phase") == "replay" and event.get("delegated") is False for event in prefix
    )


def _replay_hashes(events: list[dict[str, Any]], count: int) -> list[str | None]:
    return [event.get("output_sha256") for event in _replay_prefix(events, count)]


def _generation_started(events: list[dict[str, Any]], count: int) -> bool:
    model_events = _model_events(events)
    return (
        len(model_events) > count
        and model_events[count].get("phase") == "generation"
        and model_events[count].get("delegated") is True
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


def _outcomes_complete_or_absent(spec: SWEAgentProtocolV2LivePairSpec) -> bool:
    return (
        spec.control_resolved is None
        and spec.treatment_resolved is None
        or spec.control_resolved is not None
        and spec.treatment_resolved is not None
    )


def _copy_patch(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def run_sweagent_protocol_v2_live_pair(
    *,
    spec: SWEAgentProtocolV2LivePairSpec,
    policy: RuntimePermissionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RuntimePermissionPolicy()
    spec.output_dir.mkdir(parents=True, exist_ok=True)

    control_report_path = spec.control_dir / "protocol_v2_live_single_report.json"
    treatment_report_path = spec.treatment_dir / "protocol_v2_live_single_report.json"
    control_events_path = spec.control_dir / "protocol_v2_live_single_events.jsonl"
    treatment_events_path = spec.treatment_dir / "protocol_v2_live_single_events.jsonl"
    control_protocol_path = spec.control_dir / "protocol_v2_live_single_protocol.json"
    treatment_protocol_path = spec.treatment_dir / "protocol_v2_live_single_protocol.json"
    control_replay_path = spec.control_dir / "protocol_v2_live_single_replay_actions.json"
    treatment_replay_path = spec.treatment_dir / "protocol_v2_live_single_replay_actions.json"

    control_report = _load_json(control_report_path) if control_report_path.is_file() else {}
    treatment_report = _load_json(treatment_report_path) if treatment_report_path.is_file() else {}
    control_events = _load_jsonl(control_events_path)
    treatment_events = _load_jsonl(treatment_events_path)
    replay_count = int(control_report.get("replay_action_count") or 0)
    treatment_replay_count = int(treatment_report.get("replay_action_count") or 0)
    control_constraints = _constraint_events(control_events)
    treatment_constraints = _constraint_events(treatment_events)
    source_task_id = control_report.get("source_task_id")
    if source_task_id != treatment_report.get("source_task_id"):
        source_task_id = None
    pair_id = control_report.get("pair_id")
    if pair_id != treatment_report.get("pair_id"):
        pair_id = None

    control_patch_archive = spec.output_dir / "control" / "sweagent_protocol_v2_live_single.patch"
    treatment_patch_archive = (
        spec.output_dir / "treatment" / "sweagent_protocol_v2_live_single.patch"
    )
    if spec.control_patch.is_file():
        _copy_patch(spec.control_patch, control_patch_archive)
    if spec.treatment_patch.is_file():
        _copy_patch(spec.treatment_patch, treatment_patch_archive)

    outcomes_present = spec.control_resolved is not None or spec.treatment_resolved is not None
    official_eval_claimed = spec.outcome_source == "official_eval"
    replay_hashes_match = _replay_hashes(control_events, replay_count) == _replay_hashes(
        treatment_events, replay_count
    )
    treatment_constraint_blocked = treatment_report.get("constraint_blocked") is True or any(
        event.get("blocked") is True for event in treatment_constraints
    )
    gates = {
        "control_report_exists": control_report_path.is_file(),
        "treatment_report_exists": treatment_report_path.is_file(),
        "control_events_exist": control_events_path.is_file(),
        "treatment_events_exist": treatment_events_path.is_file(),
        "control_arm_completed": control_report.get("decision")
        == "protocol_v2_live_single_run_completed"
        and control_report.get("passed") is True,
        "treatment_arm_completed": treatment_report.get("decision")
        == "protocol_v2_live_single_run_completed"
        and treatment_report.get("passed") is True,
        "control_arm_type_valid": control_report.get("arm_type") == "control",
        "treatment_arm_type_valid": treatment_report.get("arm_type") == "treatment",
        "same_source_task_id": source_task_id is not None,
        "same_pair_id": pair_id is not None,
        "protocol_hashes_match": control_protocol_path.is_file()
        and treatment_protocol_path.is_file()
        and sha256_file(control_protocol_path) == sha256_file(treatment_protocol_path),
        "replay_action_counts_match": replay_count > 0 and replay_count == treatment_replay_count,
        "replay_config_hashes_match": control_report.get("replay_config_hash")
        == treatment_report.get("replay_config_hash"),
        "replay_artifact_hashes_match": control_replay_path.is_file()
        and treatment_replay_path.is_file()
        and sha256_file(control_replay_path) == sha256_file(treatment_replay_path),
        "control_replay_prefix_valid": _replay_prefix_valid(control_events, replay_count),
        "treatment_replay_prefix_valid": _replay_prefix_valid(treatment_events, replay_count),
        "replay_prefix_output_hashes_match": replay_hashes_match,
        "control_generation_started_after_replay": _generation_started(
            control_events, replay_count
        ),
        "treatment_generation_started_after_replay": _generation_started(
            treatment_events, replay_count
        ),
        "control_hook_absent": len(control_constraints) == 0
        and int(control_report.get("hook_event_count") or 0) == 0,
        "treatment_hook_active": len(treatment_constraints) > 0
        and int(treatment_report.get("hook_event_count") or 0) == len(treatment_constraints),
        "treatment_constraint_outcome_recorded": isinstance(treatment_constraint_blocked, bool),
        "control_patch_archive_present": control_patch_archive.is_file()
        and control_patch_archive.stat().st_size > 0,
        "treatment_patch_archive_present": treatment_patch_archive.is_file()
        and treatment_patch_archive.stat().st_size > 0,
        "outcomes_complete_or_absent": _outcomes_complete_or_absent(spec),
        "outcome_source_declared_if_outcomes_present": not outcomes_present
        or spec.outcome_source != "not_provided",
        "official_eval_ack_if_claimed": not official_eval_claimed or policy.allow_official_eval,
        "official_eval_not_run_by_pair_assembly": True,
        "state_capsule_equivalence_not_claimed": True,
        "generalized_uplift_claim_not_made": True,
    }
    structural_passed = all(gates.values())
    if not structural_passed:
        decision = "protocol_v2_live_pair_blocked"
    elif outcomes_present:
        decision = "protocol_v2_live_pair_outcome_label_ready"
    else:
        decision = "protocol_v2_live_pair_ready_pending_official_eval"

    effect_label = _effect_label(spec.control_resolved, spec.treatment_resolved)
    pair_summary = {
        "pair_id": pair_id,
        "source_task_id": source_task_id,
        "control_arm_dir": spec.control_dir.as_posix(),
        "intervention_arm_dir": spec.treatment_dir.as_posix(),
        "control_patch_archive_path": control_patch_archive.as_posix(),
        "control_patch_archive_sha256": sha256_file(control_patch_archive)
        if control_patch_archive.is_file()
        else None,
        "intervention_patch_archive_path": treatment_patch_archive.as_posix(),
        "intervention_patch_archive_sha256": sha256_file(treatment_patch_archive)
        if treatment_patch_archive.is_file()
        else None,
        "control_resolved": spec.control_resolved,
        "intervention_resolved": spec.treatment_resolved,
        "effect_label": effect_label,
        "outcome_source": spec.outcome_source,
        "main_attribution_eligible": structural_passed,
        "behavior_control_type": "protocol_v2_constraint_hook",
        "replay_action_count": replay_count,
        "replay_config_hash": control_report.get("replay_config_hash"),
        "replay_prefix_output_hashes_match": replay_hashes_match,
        "treatment_hook_event_count": len(treatment_constraints),
        "treatment_constraint_blocked": treatment_constraint_blocked,
        "state_capsule_equivalence_claimed": False,
        "single_pair_only": True,
    }

    report_path = spec.output_dir / "protocol_v2_live_pair_report.json"
    summary_path = spec.output_dir / "protocol_v2_live_pair_summary.jsonl"
    manifest_path = spec.output_dir / "protocol_v2_live_pair_manifest.json"
    report = generate_report(
        phase=SWEAGENT_PROTOCOL_V2_LIVE_PAIR_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_PROTOCOL_V2_LIVE_PAIR_VERSION,
            "claim_boundary": BOUNDARY,
            "control_dir": spec.control_dir.as_posix(),
            "treatment_dir": spec.treatment_dir.as_posix(),
            "pair_id": pair_id,
            "source_task_id": source_task_id,
            "outcome_source": spec.outcome_source,
            "control_resolved": spec.control_resolved,
            "treatment_resolved": spec.treatment_resolved,
            "effect_label": effect_label,
            "pair_summary_path": summary_path.as_posix(),
            "official_eval_started": False,
            "state_capsule_equivalence_claimed": False,
        },
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(summary_path, [pair_summary])
    artifacts = [
        _artifact(path)
        for path in [
            control_report_path,
            treatment_report_path,
            control_events_path,
            treatment_events_path,
            control_protocol_path,
            treatment_protocol_path,
            control_replay_path,
            treatment_replay_path,
            control_patch_archive,
            treatment_patch_archive,
            report_path,
            summary_path,
        ]
    ]
    manifest = generate_manifest(
        phase=SWEAGENT_PROTOCOL_V2_LIVE_PAIR_PHASE,
        report=report,
        artifacts=artifacts,
    )
    manifest["version"] = SWEAGENT_PROTOCOL_V2_LIVE_PAIR_VERSION
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
    "SWEAGENT_PROTOCOL_V2_LIVE_PAIR_PHASE",
    "SWEAGENT_PROTOCOL_V2_LIVE_PAIR_VERSION",
    "SWEAgentProtocolV2LivePairSpec",
    "run_sweagent_protocol_v2_live_pair",
]
