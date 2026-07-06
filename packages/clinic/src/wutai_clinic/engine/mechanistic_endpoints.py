"""Pre-registered secondary mechanistic endpoints for completed Protocol v2 pairs.

Retroactive, fully offline analysis layer: it reads existing pair artifacts
(patches, trajectories, official-eval per-arm reports) and computes dense
mechanistic endpoints that the binary resolved/unresolved primary endpoint
cannot capture. Gold-patch data is consumed strictly inside this analysis
layer (``oracle_data_used: gold_patch_offline_analysis_only``) and never
enters any runtime-visible configuration.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from wutai_clinic.io import read_jsonl, write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

MECHANISTIC_ENDPOINTS_PHASE = "7.mechanistic_endpoints"
MECHANISTIC_ENDPOINTS_VERSION = "phase7_mechanistic_endpoints_v1"

ORACLE_DATA_USED = "gold_patch_offline_analysis_only"

# Fixed boundary text: secondary endpoints never alter the primary conclusion.
CLAIM_BOUNDARY = (
    "Secondary mechanistic endpoints are exploratory, pre-registered for "
    "dose-response characterization only; they do not alter the primary "
    "no-uplift conclusion and support no predictive or causal claim."
)

V2_NO_UPLIFT_LABELS = {
    "both_unresolved_trigger_hit_pair_no_uplift",
    "both_resolved_trigger_hit_pair_no_uplift",
}

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)", re.MULTILINE)
_PLUS_FILE_RE = re.compile(r"^\+\+\+ b/(?P<b>\S+)", re.MULTILINE)

# ---------------------------------------------------------------------------
# Pure endpoint functions
# ---------------------------------------------------------------------------


def patch_files(patch_text: str) -> set[str]:
    """File paths touched by a unified diff (b-side of each file header)."""
    files = {m.group("b") for m in _DIFF_GIT_RE.finditer(patch_text)}
    files.update(m.group("b") for m in _PLUS_FILE_RE.finditer(patch_text))
    files.discard("/dev/null")
    return files


def gold_file_overlap(patch_text: str, gold_patch_text: str) -> dict[str, Any]:
    """Jaccard overlap between patch-touched files and gold-touched files."""
    ours = patch_files(patch_text)
    gold = patch_files(gold_patch_text)
    union = ours | gold
    inter = ours & gold
    return {
        "patch_files": sorted(ours),
        "gold_files": sorted(gold),
        "jaccard": (len(inter) / len(union)) if union else 0.0,
        "hit_any_gold_file": bool(inter),
    }


def normalized_edit_distance(text_a: str, text_b: str) -> float:
    """Line-level normalized edit distance in [0, 1] (0 = identical)."""
    ratio = difflib.SequenceMatcher(
        None, text_a.splitlines(), text_b.splitlines(), autojunk=False
    ).ratio()
    return 1.0 - ratio


def patch_size_lines(patch_text: str) -> dict[str, int]:
    """Added/removed line counts, excluding file-header +++/--- lines."""
    added = removed = 0
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed, "total": added + removed}


def fail_to_pass_partial(eval_report: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    """FAIL_TO_PASS pass counts from an official-eval per-arm report payload."""
    instance = eval_report.get(instance_id)
    if not isinstance(instance, dict):
        return None
    tests = instance.get("tests_status")
    if not isinstance(tests, dict):
        return None
    f2p = tests.get("FAIL_TO_PASS")
    if not isinstance(f2p, dict):
        return None
    passed = len(f2p.get("success") or [])
    failed = len(f2p.get("failure") or [])
    return {
        "passed": passed,
        "total": passed + failed,
        "resolved": instance.get("resolved"),
    }


def first_divergence_step(actions_a: list[str], actions_b: list[str]) -> int | None:
    """Index of the first differing action; None when sequences are identical."""
    for index, (left, right) in enumerate(zip(actions_a, actions_b)):
        if left != right:
            return index
    if len(actions_a) != len(actions_b):
        return min(len(actions_a), len(actions_b))
    return None


def arm_divergence(
    control_patch: str,
    treatment_patch: str,
    control_actions: list[str],
    treatment_actions: list[str],
) -> dict[str, Any]:
    """Pair-level divergence between the two arms (patch text + trajectory)."""
    step = first_divergence_step(control_actions, treatment_actions)
    return {
        "patch_edit_distance": normalized_edit_distance(control_patch, treatment_patch),
        "first_divergence_step": step,
        "control_action_count": len(control_actions),
        "treatment_action_count": len(treatment_actions),
        "diverged": step is not None,
    }


# ---------------------------------------------------------------------------
# Artifact discovery (read-only)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "exists": path.is_file(),
    }


def _load_fresh_candidate_task_ids(root: Path) -> set[str]:
    fresh_list = (
        root
        / "protocol_v2_fresh_candidate_gate"
        / "protocol_v2_fresh_candidate_set_candidates.jsonl"
    )
    if not fresh_list.is_file():
        return set()
    return {
        str(row["source_task_id"]) for row in read_jsonl(fresh_list) if row.get("source_task_id")
    }


def _traj_actions(traj_path: Path) -> list[str] | None:
    if not traj_path.is_file():
        return None
    payload = _load_json(traj_path)
    steps = payload.get("trajectory")
    if not isinstance(steps, list):
        return None
    return [str(step.get("action") or "") for step in steps]


def _eval_arm_report(root: Path, task_id: str, eval_arm_name: str) -> dict[str, Any] | None:
    """Find the per-arm official-eval report.json under the isolated eval tree."""
    base = root / "protocol_v2_official_eval_isolated" / task_id / "logs" / "run_evaluation"
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"*/*__{eval_arm_name}/{task_id}/report.json"))
    if not matches:
        return None
    return _load_json(matches[0])


def load_gold_patches(
    task_ids: list[str],
    *,
    offline_gold_path: Path | None = None,
    dataset_name: str = "SWE-bench/SWE-bench_Lite",
    split: str = "test",
) -> dict[str, str]:
    """Gold patch per instance: from an injected JSONL, else the local HF cache.

    Never issues network requests: the optional ``datasets`` import runs with
    HF_DATASETS_OFFLINE=1 semantics. Missing gold simply yields absent keys
    (downstream endpoints become null with ``endpoint_unavailable`` flags).
    """
    if offline_gold_path is not None:
        return {
            str(row["instance_id"]): str(row["patch"])
            for row in read_jsonl(offline_gold_path)
            if row.get("instance_id") and row.get("patch")
        }
    wanted = set(task_ids)
    try:  # pragma: no cover - exercised only with a local HF cache present
        import os

        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
        from datasets import load_dataset  # type: ignore[import-not-found]

        dataset = load_dataset(dataset_name, split=split)
        return {
            str(row["instance_id"]): str(row["patch"])
            for row in dataset
            if row["instance_id"] in wanted
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Row construction + aggregation
# ---------------------------------------------------------------------------

_ARM_EVAL_NAMES = {"control": "control", "treatment": "intervention"}


def _arm_endpoints(
    root: Path,
    task_id: str,
    arm: str,
    gold_patch: str | None,
) -> tuple[dict[str, Any], list[str], str | None, list[str] | None]:
    """Endpoints for one arm; returns (payload, unavailable, patch_text, actions)."""
    unavailable: list[str] = []
    native = root / "protocol_v2_planned_preflight" / task_id / "native" / arm / task_id
    patch_path = native / f"{task_id}.patch"
    patch_text = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else None
    actions = _traj_actions(native / f"{task_id}.traj")

    payload: dict[str, Any] = {"arm": arm}
    if patch_text is None:
        unavailable.extend(["gold_file_overlap", "gold_edit_distance", "patch_size_lines"])
        payload.update(
            {"gold_file_overlap": None, "gold_edit_distance": None, "patch_size_lines": None}
        )
    else:
        payload["patch_size_lines"] = patch_size_lines(patch_text)
        if gold_patch is None:
            unavailable.extend(["gold_file_overlap", "gold_edit_distance"])
            payload.update({"gold_file_overlap": None, "gold_edit_distance": None})
        else:
            payload["gold_file_overlap"] = gold_file_overlap(patch_text, gold_patch)
            payload["gold_edit_distance"] = normalized_edit_distance(patch_text, gold_patch)

    eval_report = _eval_arm_report(root, task_id, _ARM_EVAL_NAMES[arm])
    f2p = fail_to_pass_partial(eval_report, task_id) if eval_report else None
    if f2p is None:
        unavailable.append("fail_to_pass_partial")
    payload["fail_to_pass_partial"] = f2p
    return payload, unavailable, patch_text, actions


def build_mechanistic_rows(
    root: Path,
    gold_patches: dict[str, str],
) -> list[dict[str, Any]]:
    """One row per completed v2 pair, with per-arm and pair-level endpoints."""
    fresh_ids = _load_fresh_candidate_task_ids(root)
    eval_root = root / "protocol_v2_official_eval"
    rows: list[dict[str, Any]] = []
    if not eval_root.is_dir():
        return rows
    for scorecard_path in sorted(eval_root.glob("*/protocol_v2_dual_scorecard.json")):
        scorecard = _load_json(scorecard_path)
        task_id = str(scorecard.get("source_task_id") or scorecard_path.parent.name)
        gold = gold_patches.get(task_id)
        unavailable: list[str] = []
        arms: dict[str, dict[str, Any]] = {}
        patches: dict[str, str | None] = {}
        actions: dict[str, list[str] | None] = {}
        for arm in ("control", "treatment"):
            payload, arm_missing, patch_text, arm_actions = _arm_endpoints(root, task_id, arm, gold)
            arms[arm] = payload
            patches[arm] = patch_text
            actions[arm] = arm_actions
            unavailable.extend(f"{arm}.{name}" for name in arm_missing)

        if (
            patches["control"] is not None
            and patches["treatment"] is not None
            and actions["control"] is not None
            and actions["treatment"] is not None
        ):
            divergence: dict[str, Any] | None = arm_divergence(
                patches["control"],
                patches["treatment"],
                actions["control"],
                actions["treatment"],
            )
        else:
            divergence = None
            unavailable.append("arm_divergence")

        rows.append(
            {
                "source_task_id": task_id,
                "pair_id": scorecard.get("pair_id"),
                "lineage": "v2_strict_fresh" if task_id in fresh_ids else "v2_reference",
                "effect_label": scorecard.get("effect_label"),
                "official_eval_completed": scorecard.get("official_eval_completed") is True,
                "control": arms["control"],
                "treatment": arms["treatment"],
                "arm_divergence": divergence,
                "gold_available": gold is not None,
                "endpoints_unavailable": sorted(unavailable),
                "scorecard_path": scorecard_path.as_posix(),
            }
        )
    return rows


def mechanistic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    strict = [r for r in rows if r["lineage"] == "v2_strict_fresh"]
    reference = [r for r in rows if r["lineage"] == "v2_reference"]
    diverged = [r for r in rows if (r.get("arm_divergence") or {}).get("diverged")]
    return {
        "pair_count": len(rows),
        "strict_fresh_pair_count": len(strict),
        "reference_pair_count": len(reference),
        "diverged_pair_count": len(diverged),
        "gold_available_pair_count": sum(1 for r in rows if r["gold_available"]),
        "any_gold_file_hit_pair_count": sum(
            1
            for r in rows
            if any(
                ((r[arm].get("gold_file_overlap") or {}).get("hit_any_gold_file"))
                for arm in ("control", "treatment")
            )
        ),
        "primary_outcome_no_uplift_all": bool(rows)
        and all(str(r.get("effect_label") or "") in V2_NO_UPLIFT_LABELS for r in rows),
    }


def mechanistic_decision(summary: dict[str, Any]) -> str:
    if summary["pair_count"] == 0:
        return "mechanistic_endpoints_blocked_no_pairs"
    if summary["diverged_pair_count"] > 0 and summary["primary_outcome_no_uplift_all"]:
        return "mechanistic_endpoints_ready_divergence_without_outcome_change"
    if summary["diverged_pair_count"] == 0:
        return "mechanistic_endpoints_ready_no_divergence_observed"
    return "mechanistic_endpoints_ready_mixed_primary_outcomes"


def mechanistic_gates(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, bool]:
    return {
        "mechanistic_rows_present": summary["pair_count"] > 0,
        "official_eval_completed_all_rows": bool(rows)
        and all(r["official_eval_completed"] for r in rows),
        "patch_artifacts_resolved_all_rows": bool(rows)
        and all("arm_divergence" not in r["endpoints_unavailable"] for r in rows),
    }


# ---------------------------------------------------------------------------
# Evidence writer
# ---------------------------------------------------------------------------


def write_mechanistic_endpoints_evidence(
    root: Path,
    output_dir: Path,
    *,
    offline_gold_path: Path | None = None,
    dataset_name: str = "SWE-bench/SWE-bench_Lite",
    split: str = "test",
) -> dict[str, Any]:
    """Scan EVIDENCE_ROOT (read-only) and write report/pairs/manifest artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_root = root / "protocol_v2_official_eval"
    task_ids = (
        sorted(p.parent.name for p in eval_root.glob("*/protocol_v2_dual_scorecard.json"))
        if eval_root.is_dir()
        else []
    )
    gold_patches = load_gold_patches(
        task_ids,
        offline_gold_path=offline_gold_path,
        dataset_name=dataset_name,
        split=split,
    )
    rows = build_mechanistic_rows(root, gold_patches)
    summary = mechanistic_summary(rows)
    decision = mechanistic_decision(summary)
    gates = mechanistic_gates(rows, summary)

    report = generate_report(
        phase=MECHANISTIC_ENDPOINTS_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": MECHANISTIC_ENDPOINTS_VERSION,
            "summary": summary,
            "claim_boundary": CLAIM_BOUNDARY,
            "oracle_data_used": ORACLE_DATA_USED,
            "gold_source": (
                offline_gold_path.as_posix()
                if offline_gold_path is not None
                else f"{dataset_name}:{split} (local HF cache, offline)"
            ),
            "evidence_root": root.as_posix(),
        },
    )

    pairs_path = output_dir / "mechanistic_endpoints_pairs.jsonl"
    write_jsonl(pairs_path, rows)
    report_path = output_dir / "mechanistic_endpoints_report.json"
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=MECHANISTIC_ENDPOINTS_PHASE,
        report=report,
        artifacts=[_artifact(report_path), _artifact(pairs_path)],
    )
    manifest_path = output_dir / "mechanistic_endpoints_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "rows": rows,
        "report_path": report_path,
        "pairs_path": pairs_path,
        "manifest_path": manifest_path,
    }
