from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.mechanistic_endpoints import (
    CLAIM_BOUNDARY,
    arm_divergence,
    fail_to_pass_partial,
    first_divergence_step,
    gold_file_overlap,
    normalized_edit_distance,
    patch_files,
    patch_size_lines,
    write_mechanistic_endpoints_evidence,
)

runner = CliRunner()

CONTROL_PATCH = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
+import os
 def main():
-    return 1
+    return 2
"""

TREATMENT_PATCH = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
+import sys
 def main():
-    return 1
+    return 3
"""

GOLD_PATCH = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,3 @@
 def main():
-    return 1
+    return 42
diff --git a/src/other.py b/src/other.py
--- a/src/other.py
+++ b/src/other.py
@@ -1 +1 @@
-x = 1
+x = 2
"""


# ---------------------------------------------------------------------------
# Pure endpoint functions
# ---------------------------------------------------------------------------


def test_patch_files_extracts_b_side() -> None:
    assert patch_files(GOLD_PATCH) == {"src/app.py", "src/other.py"}


def test_gold_file_overlap_jaccard() -> None:
    overlap = gold_file_overlap(CONTROL_PATCH, GOLD_PATCH)
    # control touches {app.py}, gold touches {app.py, other.py} -> 1/2.
    assert overlap["jaccard"] == 0.5
    assert overlap["hit_any_gold_file"] is True


def test_gold_file_overlap_no_hit() -> None:
    other = CONTROL_PATCH.replace("src/app.py", "src/unrelated.py")
    overlap = gold_file_overlap(other, GOLD_PATCH)
    assert overlap["jaccard"] == 0.0
    assert overlap["hit_any_gold_file"] is False


def test_normalized_edit_distance_bounds() -> None:
    assert normalized_edit_distance(CONTROL_PATCH, CONTROL_PATCH) == 0.0
    assert 0.0 < normalized_edit_distance(CONTROL_PATCH, TREATMENT_PATCH) < 1.0
    assert normalized_edit_distance("a\nb", "") == 1.0


def test_patch_size_lines_excludes_headers() -> None:
    size = patch_size_lines(CONTROL_PATCH)
    assert size == {"added": 2, "removed": 1, "total": 3}


def test_fail_to_pass_partial() -> None:
    payload = {
        "inst-1": {
            "resolved": False,
            "tests_status": {
                "FAIL_TO_PASS": {"success": ["t1"], "failure": ["t2", "t3"]},
                "PASS_TO_PASS": {"success": [], "failure": []},
            },
        }
    }
    result = fail_to_pass_partial(payload, "inst-1")
    assert result == {"passed": 1, "total": 3, "resolved": False}
    assert fail_to_pass_partial(payload, "missing") is None
    assert fail_to_pass_partial({"inst-1": {}}, "inst-1") is None


def test_first_divergence_step() -> None:
    assert first_divergence_step(["a", "b"], ["a", "b"]) is None
    assert first_divergence_step(["a", "b"], ["a", "c"]) == 1
    # Prefix relationship diverges at the shorter length.
    assert first_divergence_step(["a"], ["a", "b"]) == 1


def test_arm_divergence_diverged_flag() -> None:
    result = arm_divergence(CONTROL_PATCH, TREATMENT_PATCH, ["ls", "cat"], ["ls", "vim"])
    assert result["diverged"] is True
    assert result["first_divergence_step"] == 1
    assert result["patch_edit_distance"] > 0.0
    identical = arm_divergence(CONTROL_PATCH, CONTROL_PATCH, ["ls"], ["ls"])
    assert identical["diverged"] is False
    assert identical["patch_edit_distance"] == 0.0


# ---------------------------------------------------------------------------
# Synthetic evidence tree
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _make_evidence_tree(
    root: Path,
    task_id: str = "demo__pkg-1",
    *,
    in_fresh_list: bool = True,
    with_eval_logs: bool = True,
    with_patches: bool = True,
) -> None:
    pair_id = "pair_001_failure_target_demo"
    _write_json(
        root / "protocol_v2_official_eval" / task_id / "protocol_v2_dual_scorecard.json",
        {
            "source_task_id": task_id,
            "pair_id": pair_id,
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "official_eval_completed": True,
        },
    )
    if in_fresh_list:
        fresh = root / "protocol_v2_fresh_candidate_gate"
        fresh.mkdir(parents=True, exist_ok=True)
        (fresh / "protocol_v2_fresh_candidate_set_candidates.jsonl").write_text(
            json.dumps({"source_task_id": task_id}) + "\n", encoding="utf-8"
        )
    if with_patches:
        for arm, patch, actions in (
            ("control", CONTROL_PATCH, ["ls", "cat x", "submit"]),
            ("treatment", TREATMENT_PATCH, ["ls", "grep y", "submit"]),
        ):
            native = root / "protocol_v2_planned_preflight" / task_id / "native" / arm / task_id
            native.mkdir(parents=True, exist_ok=True)
            (native / f"{task_id}.patch").write_text(patch, encoding="utf-8")
            _write_json(
                native / f"{task_id}.traj",
                {"trajectory": [{"action": action} for action in actions]},
            )
    if with_eval_logs:
        for eval_arm, passed in (("control", 0), ("intervention", 1)):
            log_dir = (
                root
                / "protocol_v2_official_eval_isolated"
                / task_id
                / "logs"
                / "run_evaluation"
                / "run_1"
                / f"eval__{pair_id}__{eval_arm}"
                / task_id
            )
            _write_json(
                log_dir / "report.json",
                {
                    task_id: {
                        "resolved": False,
                        "tests_status": {
                            "FAIL_TO_PASS": {
                                "success": ["t_ok"] * passed,
                                "failure": ["t_fail"],
                            },
                            "PASS_TO_PASS": {"success": [], "failure": []},
                        },
                    }
                },
            )


def _gold_jsonl(path: Path, task_id: str = "demo__pkg-1") -> Path:
    path.write_text(
        json.dumps({"instance_id": task_id, "patch": GOLD_PATCH}) + "\n", encoding="utf-8"
    )
    return path


def test_write_evidence_full_tree(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root)
    gold = _gold_jsonl(tmp_path / "gold.jsonl")
    result = write_mechanistic_endpoints_evidence(root, tmp_path / "out", offline_gold_path=gold)
    report = result["report"]
    assert report["decision"] == "mechanistic_endpoints_ready_divergence_without_outcome_change"
    assert report["passed"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert report["oracle_data_used"] == "gold_patch_offline_analysis_only"
    (row,) = result["rows"]
    assert row["lineage"] == "v2_strict_fresh"
    assert row["arm_divergence"]["diverged"] is True
    assert row["arm_divergence"]["first_divergence_step"] == 1
    assert row["control"]["fail_to_pass_partial"] == {
        "passed": 0,
        "total": 1,
        "resolved": False,
    }
    assert row["treatment"]["fail_to_pass_partial"]["passed"] == 1
    assert row["control"]["gold_file_overlap"]["hit_any_gold_file"] is True
    assert row["endpoints_unavailable"] == []


def test_reference_lineage_when_not_in_fresh_list(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root, in_fresh_list=False)
    result = write_mechanistic_endpoints_evidence(
        root, tmp_path / "out", offline_gold_path=_gold_jsonl(tmp_path / "gold.jsonl")
    )
    (row,) = result["rows"]
    assert row["lineage"] == "v2_reference"
    assert result["report"]["summary"]["reference_pair_count"] == 1


def test_gold_missing_fallback(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root)
    empty_gold = tmp_path / "gold.jsonl"
    empty_gold.write_text("", encoding="utf-8")
    result = write_mechanistic_endpoints_evidence(
        root, tmp_path / "out", offline_gold_path=empty_gold
    )
    (row,) = result["rows"]
    assert row["gold_available"] is False
    assert row["control"]["gold_file_overlap"] is None
    assert "control.gold_edit_distance" in row["endpoints_unavailable"]
    # Gold-independent endpoints still compute.
    assert row["arm_divergence"]["diverged"] is True
    assert result["report"]["passed"] is True


def test_eval_logs_missing_fallback(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root, with_eval_logs=False)
    result = write_mechanistic_endpoints_evidence(
        root, tmp_path / "out", offline_gold_path=_gold_jsonl(tmp_path / "gold.jsonl")
    )
    (row,) = result["rows"]
    assert row["control"]["fail_to_pass_partial"] is None
    assert "control.fail_to_pass_partial" in row["endpoints_unavailable"]


def test_patches_missing_blocks_divergence_gate(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root, with_patches=False)
    result = write_mechanistic_endpoints_evidence(
        root, tmp_path / "out", offline_gold_path=_gold_jsonl(tmp_path / "gold.jsonl")
    )
    report = result["report"]
    assert report["gates"]["patch_artifacts_resolved_all_rows"] is False
    assert report["passed"] is False
    (row,) = result["rows"]
    assert row["arm_divergence"] is None


def test_empty_root_blocked_decision(tmp_path: Path) -> None:
    result = write_mechanistic_endpoints_evidence(
        tmp_path / "missing", tmp_path / "out", offline_gold_path=None
    )
    assert result["report"]["decision"] == "mechanistic_endpoints_blocked_no_pairs"
    assert result["report"]["passed"] is False


def test_cli_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _make_evidence_tree(root)
    gold = _gold_jsonl(tmp_path / "gold.jsonl")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "mechanistic-endpoints",
            str(root),
            "-o",
            str(out),
            "--offline-gold",
            str(gold),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["pair_count"] == 1
    assert payload["diverged_pair_count"] == 1
    assert (out / "mechanistic_endpoints_report.json").is_file()
    assert (out / "mechanistic_endpoints_pairs.jsonl").is_file()
    assert (out / "mechanistic_endpoints_manifest.json").is_file()
    report = json.loads((out / "mechanistic_endpoints_report.json").read_text())
    assert "claim_boundary" in report and "decision" in report
