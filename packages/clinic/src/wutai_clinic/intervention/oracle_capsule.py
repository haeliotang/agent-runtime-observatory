"""Oracle capsule probe: contaminated-by-design ceiling experiment for mediation.

Distills the SWE-bench gold patch into natural-language guidance (never the raw
diff) and injects it into a cloned control-arm runtime config. Comparing the
oracle arm against the existing control and diagnostic-treatment arms localizes
the causal bottleneck between the injection channel and diagnostic content.

Isolation invariants (violations void the layer):
- All probe artifacts live under ``protocol_v2_oracle_probe/`` and are excluded
  from every strict-fresh/reference/uplift statistic.
- Reports carry ``contaminated_by_design: true`` and manifests carry
  ``oracle_derived: true``.
- Runtime configs never contain ``official_test_id`` / resolved oracle /
  ``raw_payload``; oracle information travels only as distilled capsule text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

ORACLE_PROBE_PHASE = "7.oracle_capsule_probe"
ORACLE_PROBE_VERSION = "phase7_oracle_capsule_probe_v1"
ORACLE_PROBE_LAYER = "v2_oracle_probe"

CLAIM_BOUNDARY = (
    "Oracle-probe arms are contaminated by design and exist solely to localize "
    "the causal bottleneck between injection channel and diagnostic content. "
    "No outcome from this layer supports any intervention-effectiveness claim."
)

_FORBIDDEN_CONFIG_TOKENS = ("official_test_id", "raw_payload", "FAIL_TO_PASS", "PASS_TO_PASS")

_HUNK_HEADER_RE = re.compile(r"^@@ [^@]*@@ ?(?P<context>.*)$")
_HUNK_RANGE_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? \+(?P<new_start>\d+)")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Change-kind keywords detectable from added lines without quoting them.
_CHANGE_KIND_TOKENS = {
    "raise": "introduces an explicit error raise",
    "assert": "adds an assertion",
    "return": "changes a return path",
    "if ": "adds or alters a conditional guard",
    "def ": "adds or modifies a function definition",
    "class ": "adds or modifies a class definition",
    "import": "touches imports",
}


def _parse_gold_hunks(gold_patch_text: str) -> dict[str, list[dict[str, Any]]]:
    """Per-file hunk descriptors: context line, added/removed counts, change kinds."""
    files: dict[str, list[dict[str, Any]]] = {}
    current_file: str | None = None
    current: dict[str, Any] | None = None
    for line in gold_patch_text.splitlines():
        if line.startswith("diff --git"):
            match = re.search(r" b/(\S+)$", line)
            current_file = match.group(1) if match else None
            current = None
            if current_file is not None:
                files.setdefault(current_file, [])
            continue
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            files.setdefault(current_file, [])
            continue
        header = _HUNK_HEADER_RE.match(line)
        if header and current_file is not None:
            range_match = _HUNK_RANGE_RE.match(line)
            current = {
                "context": header.group("context").strip(),
                "added": 0,
                "removed": 0,
                "kinds": set(),
                "old_start": int(range_match.group("old_start")) if range_match else None,
                "added_identifiers": set(),
                "removed_identifiers": set(),
            }
            files[current_file].append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"] += 1
            stripped = line[1:].strip()
            current["added_identifiers"].update(_IDENTIFIER_RE.findall(stripped))
            lowered = stripped.lower()
            for token, description in _CHANGE_KIND_TOKENS.items():
                if lowered.startswith(token) or f" {token}" in f" {lowered}":
                    current["kinds"].add(description)
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"] += 1
            current["removed_identifiers"].update(_IDENTIFIER_RE.findall(line[1:].strip()))
    return files


DISTILLATION_LEVELS = ("guidance", "detailed", "verbatim")


def distill_gold_to_capsule(gold_patch_text: str, level: str = "guidance") -> str:
    """Rule-based distillation of a gold patch at three dose levels.

    guidance  — files + hunk context + change kinds; no identifiers from bodies.
    detailed  — adds exact line ranges and identifiers extracted from changed
                lines (never the lines themselves); same no-quote invariant.
    verbatim  — explicit-marked inclusion of the raw patch (dose-response
                ceiling, task11); exempt from the no-quote invariant but still
                subject to forbidden-token config checks downstream.
    """
    if level not in DISTILLATION_LEVELS:
        raise ValueError(f"unknown distillation level: {level}")
    if level == "verbatim":
        return "\n".join(
            [
                "[ORACLE-PROBE VERBATIM — contaminated-by-design analysis layer]",
                "The verified fix for this issue, as a unified diff:",
                "```diff",
                gold_patch_text.rstrip("\n"),
                "```",
                "Reproduce the failure first, then apply exactly this change.",
            ]
        )
    files = _parse_gold_hunks(gold_patch_text)
    lines = [
        "[ORACLE-PROBE GUIDANCE — contaminated-by-design analysis layer]",
        "High-confidence guidance about where the verified fix for this issue lives:",
    ]
    for file_path, hunks in files.items():
        lines.append(f"- File `{file_path}` ({len(hunks)} change region(s)):")
        for hunk in hunks:
            descriptors = []
            if hunk["context"]:
                identifiers = _IDENTIFIER_RE.findall(hunk["context"])
                if identifiers:
                    descriptors.append(f"near `{identifiers[-1]}`")
            if level == "detailed" and hunk.get("old_start") is not None:
                descriptors.append(f"around line {hunk['old_start']}")
            descriptors.append(f"about {hunk['added']} line(s) added, {hunk['removed']} removed")
            if hunk["kinds"]:
                descriptors.append("; ".join(sorted(hunk["kinds"])))
            lines.append(f"    * {', '.join(descriptors)}")
            if level == "detailed":
                new_identifiers = sorted(hunk["added_identifiers"] - hunk["removed_identifiers"])
                shared = sorted(hunk["added_identifiers"] & hunk["removed_identifiers"])
                if new_identifiers:
                    lines.append(
                        "      the new code introduces or references: "
                        + ", ".join(f"`{name}`" for name in new_identifiers[:12])
                    )
                if shared:
                    lines.append(
                        "      the change rewrites code involving: "
                        + ", ".join(f"`{name}`" for name in shared[:12])
                    )
    lines.append(
        "Reproduce the failure first, then implement the equivalent behavior in "
        "exactly these locations; avoid editing any other file."
    )
    capsule = "\n".join(lines)
    # Hard invariant (guidance/detailed): never quote diff lines verbatim.
    for raw_line in gold_patch_text.splitlines():
        stripped = raw_line[1:].strip()
        if raw_line.startswith(("+", "-")) and len(stripped) >= 8 and stripped in capsule:
            raise ValueError("capsule distillation leaked a raw diff line")
    return capsule


def build_oracle_probe_runtime_config(
    base_config: dict[str, Any],
    *,
    capsule_text: str,
    native_output_dir: Path,
    distillation_level: str = "guidance",
) -> dict[str, Any]:
    """Clone a control-arm runtime config, inject the capsule, redirect output."""
    payload = json.loads(json.dumps(base_config))  # deep copy via round-trip
    problem = payload.get("problem_statement")
    if not isinstance(problem, dict) or "text" not in problem:
        raise ValueError("base config lacks problem_statement.text")
    problem["text"] = f"{problem['text']}\n\n{capsule_text}\n"
    payload["output_dir"] = native_output_dir.as_posix()
    clinic = dict(payload.get("wutai_clinic") or {})
    clinic["arm_type"] = "oracle_treatment"
    clinic["oracle_probe"] = {
        "oracle_derived": True,
        "distillation_level": distillation_level,
        "layer": ORACLE_PROBE_LAYER,
        "contaminated_by_design": True,
    }
    payload["wutai_clinic"] = clinic
    serialized = json.dumps(payload)
    for token in _FORBIDDEN_CONFIG_TOKENS:
        if token in serialized:
            raise ValueError(f"oracle probe config violates isolation: contains {token}")
    return payload


def write_oracle_probe_prepare_evidence(
    root: Path,
    *,
    source_task_id: str,
    output_dir: Path,
    gold_patches: dict[str, str],
    distillation_level: str = "guidance",
) -> dict[str, Any]:
    """Offline prepare step: capsule + cloned config + report/manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base_config_path = (
        root
        / "protocol_v2_planned_preflight"
        / source_task_id
        / "control"
        / "protocol_v2_runtime_config.json"
    )
    gold = gold_patches.get(source_task_id)
    gates = {
        "control_preflight_config_present": base_config_path.is_file(),
        "gold_patch_available": gold is not None,
    }
    capsule_path = output_dir / "oracle_capsule.txt"
    config_path = output_dir / "oracle_probe_runtime_config.json"
    if all(gates.values()):
        capsule = distill_gold_to_capsule(gold or "", level=distillation_level)
        base_config = json.loads(base_config_path.read_text(encoding="utf-8"))
        probe_config = build_oracle_probe_runtime_config(
            base_config,
            capsule_text=capsule,
            native_output_dir=output_dir / "native",
            distillation_level=distillation_level,
        )
        capsule_path.write_text(capsule + "\n", encoding="utf-8")
        config_path.write_text(
            json.dumps(probe_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        gates["capsule_contains_no_raw_diff_lines"] = True
        gates["config_isolation_invariant_held"] = True
        decision = "oracle_probe_prepared_live_execution_not_authorized"
    else:
        decision = "oracle_probe_prepare_blocked_missing_inputs"

    report = generate_report(
        phase=ORACLE_PROBE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": ORACLE_PROBE_VERSION,
            "layer": ORACLE_PROBE_LAYER,
            "source_task_id": source_task_id,
            "contaminated_by_design": True,
            "oracle_derived": True,
            "distillation_level": distillation_level,
            "claim_boundary": CLAIM_BOUNDARY,
        },
    )
    report_path = output_dir / "oracle_probe_prepare_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = generate_manifest(
        phase=ORACLE_PROBE_PHASE,
        report=report,
        artifacts=[
            {
                "path": path.as_posix(),
                "sha256": sha256_file(path) if path.is_file() else None,
                "record_count": None,
            }
            for path in [report_path, capsule_path, config_path]
        ],
    )
    manifest["oracle_derived"] = True
    manifest["contaminated_by_design"] = True
    manifest_path = output_dir / "oracle_probe_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {
        "report": report,
        "report_path": report_path,
        "config_path": config_path,
        "capsule_path": capsule_path,
        "manifest_path": manifest_path,
    }


def write_oracle_probe_outcome_evidence(
    root: Path,
    *,
    source_task_id: str,
    oracle_eval_report_path: Path,
    output_dir: Path,
    variant: str = "with_replay_prefix",
) -> dict[str, Any]:
    """Three-arm comparison: existing control + diagnostic treatment vs oracle arm."""
    output_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = (
        root / "protocol_v2_official_eval" / source_task_id / "protocol_v2_dual_scorecard.json"
    )
    scorecard = (
        json.loads(scorecard_path.read_text(encoding="utf-8")) if scorecard_path.is_file() else {}
    )
    oracle_resolved: bool | None = None
    if oracle_eval_report_path.is_file():
        payload = json.loads(oracle_eval_report_path.read_text(encoding="utf-8"))
        instance = payload.get(source_task_id)
        if isinstance(instance, dict) and "resolved" in instance:
            oracle_resolved = bool(instance["resolved"])

    gates = {
        "existing_pair_scorecard_present": bool(scorecard),
        "oracle_eval_outcome_present": oracle_resolved is not None,
    }
    if oracle_resolved is None:
        decision = "oracle_probe_outcome_blocked_missing_eval"
    elif oracle_resolved:
        decision = "oracle_probe_outcome_moved_channel_validated"
    else:
        decision = "oracle_probe_outcome_unmoved_channel_bottleneck_implicated"

    report = generate_report(
        phase=ORACLE_PROBE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": ORACLE_PROBE_VERSION,
            "layer": ORACLE_PROBE_LAYER,
            "source_task_id": source_task_id,
            "contaminated_by_design": True,
            "oracle_derived": True,
            "variant": variant,
            "claim_boundary": CLAIM_BOUNDARY,
            "three_arm_outcomes": {
                "control_resolved": scorecard.get("control_resolved"),
                "diagnostic_treatment_resolved": scorecard.get("treatment_resolved"),
                "oracle_treatment_resolved": oracle_resolved,
            },
            "interpretation_matrix": {
                "oracle_probe_outcome_moved_channel_validated": (
                    "Channel can steer outcomes; bottleneck sits in the "
                    "diagnosis-to-prescription content path."
                ),
                "oracle_probe_outcome_unmoved_channel_bottleneck_implicated": (
                    "Even maximal-information content does not move the outcome; "
                    "injection channel/timing/capacity is implicated."
                ),
            },
        },
    )
    report_path = output_dir / "oracle_probe_outcome_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = generate_manifest(
        phase=ORACLE_PROBE_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": None,
            }
        ],
    )
    manifest["oracle_derived"] = True
    manifest["contaminated_by_design"] = True
    manifest_path = output_dir / "oracle_probe_outcome_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


def build_replay_free_variant_config(
    probe_config: dict[str, Any],
    *,
    native_output_dir: Path,
) -> dict[str, Any]:
    """Clone an oracle probe config for the task10 replay-free arm.

    Capsule text and model settings stay byte-identical; only the output
    directory moves and the ``replay_prefix`` marker flips to ``none``.
    """
    payload = json.loads(json.dumps(probe_config))  # deep copy via round-trip
    probe = (payload.get("wutai_clinic") or {}).get("oracle_probe")
    if not isinstance(probe, dict) or probe.get("oracle_derived") is not True:
        raise ValueError("base config is not an oracle probe runtime config")
    payload["output_dir"] = native_output_dir.as_posix()
    probe["replay_prefix"] = "none"
    probe["variant"] = "replay_free"
    serialized = json.dumps(payload)
    for token in _FORBIDDEN_CONFIG_TOKENS:
        if token in serialized:
            raise ValueError(f"oracle probe config violates isolation: contains {token}")
    return payload


# Preregistered task10 typing threshold (see task10 brief): a patch counts as
# near-gold when it touches at least one gold file AND edit distance < 0.5.
REPLAY_FREE_NEAR_GOLD_DISTANCE = 0.5


def classify_replay_free_probe(
    *,
    oracle_resolved: bool | None,
    patch_text: str | None,
    gold_patch_text: str | None,
) -> dict[str, Any]:
    """Three-way preregistered typing of a replay-free oracle arm outcome."""
    from wutai_clinic.engine.mechanistic_endpoints import (
        gold_file_overlap,
        normalized_edit_distance,
    )

    proximity: dict[str, Any] | None = None
    near_gold: bool | None = None
    if patch_text is not None and gold_patch_text:
        overlap = gold_file_overlap(patch_text, gold_patch_text)
        distance = normalized_edit_distance(patch_text, gold_patch_text)
        near_gold = bool(overlap["hit_any_gold_file"] and distance < REPLAY_FREE_NEAR_GOLD_DISTANCE)
        proximity = {
            "gold_file_overlap": overlap,
            "gold_edit_distance": distance,
            "near_gold_threshold": REPLAY_FREE_NEAR_GOLD_DISTANCE,
            "near_gold": near_gold,
        }

    if oracle_resolved is None:
        decision = "oracle_probe_replay_free_blocked_missing_eval"
    elif oracle_resolved:
        decision = "oracle_probe_replay_free_outcome_moved_prefix_lockin_implicated"
    elif near_gold is True:
        decision = "oracle_probe_replay_free_unmoved_capability_ceiling_implicated"
    elif near_gold is False:
        decision = "oracle_probe_replay_free_unmoved_channel_capacity_implicated"
    else:
        decision = "oracle_probe_replay_free_blocked_missing_patch_or_gold"
    return {"decision": decision, "proximity": proximity}


def write_oracle_probe_replay_free_outcome_evidence(
    root: Path,
    *,
    source_task_id: str,
    oracle_eval_report_path: Path,
    replay_free_patch_path: Path,
    gold_patches: dict[str, str],
    output_dir: Path,
    variant: str = "replay_free",
) -> dict[str, Any]:
    """Task10 typing report: replay-free arm vs preregistered three-way matrix."""
    output_dir.mkdir(parents=True, exist_ok=True)
    oracle_resolved: bool | None = None
    if oracle_eval_report_path.is_file():
        payload = json.loads(oracle_eval_report_path.read_text(encoding="utf-8"))
        instance = payload.get(source_task_id)
        if isinstance(instance, dict) and "resolved" in instance:
            oracle_resolved = bool(instance["resolved"])
    patch_text = (
        replay_free_patch_path.read_text(encoding="utf-8")
        if replay_free_patch_path.is_file()
        else None
    )
    typing = classify_replay_free_probe(
        oracle_resolved=oracle_resolved,
        patch_text=patch_text,
        gold_patch_text=gold_patches.get(source_task_id),
    )
    gates = {
        "oracle_eval_outcome_present": oracle_resolved is not None,
        "replay_free_patch_present": patch_text is not None,
        "gold_patch_available": source_task_id in gold_patches,
    }
    report = generate_report(
        phase=ORACLE_PROBE_PHASE,
        decision=typing["decision"],
        gate_results=gates,
        extras={
            "version": ORACLE_PROBE_VERSION,
            "layer": ORACLE_PROBE_LAYER,
            "source_task_id": source_task_id,
            "contaminated_by_design": True,
            "oracle_derived": True,
            "variant": variant,
            "claim_boundary": CLAIM_BOUNDARY,
            "three_arm_outcomes": {"oracle_treatment_resolved": oracle_resolved},
            "proximity": typing["proximity"],
            "interpretation_matrix": {
                "oracle_probe_replay_free_outcome_moved_prefix_lockin_implicated": (
                    "Removing the replay prefix moves the outcome; trajectory "
                    "lock-in from the prefix is the bottleneck."
                ),
                "oracle_probe_replay_free_unmoved_capability_ceiling_implicated": (
                    "Patch lands near gold yet stays unresolved; the agent cannot "
                    "finish the semantics even when told where — capability ceiling."
                ),
                "oracle_probe_replay_free_unmoved_channel_capacity_implicated": (
                    "Patch stays far from gold despite step-0 guidance; the "
                    "injection channel truly fails to steer the trajectory."
                ),
            },
        },
    )
    report_path = output_dir / "oracle_probe_outcome_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = generate_manifest(
        phase=ORACLE_PROBE_PHASE,
        report=report,
        artifacts=[
            {
                "path": report_path.as_posix(),
                "sha256": sha256_file(report_path),
                "record_count": None,
            }
        ],
    )
    manifest["oracle_derived"] = True
    manifest["contaminated_by_design"] = True
    manifest_path = output_dir / "oracle_probe_outcome_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {"report": report, "report_path": report_path, "manifest_path": manifest_path}


def load_oracle_probe_rows(root: Path) -> list[dict[str, Any]]:
    """Oracle-probe outcome rows for explicit excluded listing in aggregations."""
    rows: list[dict[str, Any]] = []
    probe_root = root / "protocol_v2_oracle_probe"
    if not probe_root.is_dir():
        return rows
    # Outcome reports may sit at <target>/ or <target>/outcome/ depth.
    for report_path in sorted(probe_root.rglob("oracle_probe_outcome_report.json")):
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "source_task_id": payload.get("source_task_id"),
                "decision": payload.get("decision"),
                "layer": ORACLE_PROBE_LAYER,
                "variant": payload.get("variant", "with_replay_prefix"),
                "contaminated_by_design": True,
                "oracle_treatment_resolved": (payload.get("three_arm_outcomes") or {}).get(
                    "oracle_treatment_resolved"
                ),
                "report_path": report_path.as_posix(),
            }
        )
    return rows


__all__ = [
    "CLAIM_BOUNDARY",
    "ORACLE_PROBE_LAYER",
    "ORACLE_PROBE_VERSION",
    "REPLAY_FREE_NEAR_GOLD_DISTANCE",
    "build_oracle_probe_runtime_config",
    "build_replay_free_variant_config",
    "classify_replay_free_probe",
    "distill_gold_to_capsule",
    "load_oracle_probe_rows",
    "write_oracle_probe_outcome_evidence",
    "write_oracle_probe_prepare_evidence",
    "write_oracle_probe_replay_free_outcome_evidence",
]
