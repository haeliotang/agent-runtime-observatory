from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.oracle_capsule import (
    CLAIM_BOUNDARY,
    ORACLE_PROBE_LAYER,
    build_oracle_probe_runtime_config,
    build_replay_free_variant_config,
    classify_replay_free_probe,
    distill_gold_to_capsule,
    load_oracle_probe_rows,
    write_oracle_probe_outcome_evidence,
    write_oracle_probe_prepare_evidence,
    write_oracle_probe_replay_free_outcome_evidence,
)

runner = CliRunner()

GOLD_PATCH = """\
diff --git a/sphinx/util/docutils.py b/sphinx/util/docutils.py
--- a/sphinx/util/docutils.py
+++ b/sphinx/util/docutils.py
@@ -400,10 +400,12 @@ class SphinxRole:
     def run(self) -> tuple[list[Node], list[system_message]]:
         pass
-    def _fix(self):
-        return None
+    def _fix(self, ctx):
+        return ctx.default
+    def _validate(self, val):
+        if not val:
+            raise ValueError("empty")

diff --git a/sphinx/environment/__init__.py b/sphinx/environment/__init__.py
--- a/sphinx/environment/__init__.py
+++ b/sphinx/environment/__init__.py
@@ -1,3 +1,4 @@
+import logging
 class BuildEnvironment:
     pass
"""


def test_distill_no_raw_diff_lines() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH)
    for line in GOLD_PATCH.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            stripped = line[1:].strip()
            if len(stripped) >= 8:
                assert stripped not in capsule, f"raw diff line leaked into capsule: {stripped!r}"


def test_distill_contains_file_references() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH)
    assert "sphinx/util/docutils.py" in capsule
    assert "sphinx/environment/__init__.py" in capsule


def test_distill_contains_change_descriptors() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH)
    # Should mention line counts or change kinds — not the raw code.
    assert "added" in capsule or "removed" in capsule or "region" in capsule


def test_distill_contains_isolation_header() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH)
    assert "ORACLE-PROBE GUIDANCE" in capsule
    assert "contaminated-by-design" in capsule


def test_distill_minimal_patch() -> None:
    mini = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    capsule = distill_gold_to_capsule(mini)
    assert "x.py" in capsule
    assert "old" not in capsule
    assert "new" not in capsule


# ---------------------------------------------------------------------------
# Task11: dose levels
# ---------------------------------------------------------------------------


def test_distill_detailed_no_raw_diff_lines() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH, level="detailed")
    for line in GOLD_PATCH.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            stripped = line[1:].strip()
            if len(stripped) >= 8:
                assert stripped not in capsule


def test_distill_detailed_includes_line_ranges_and_identifiers() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH, level="detailed")
    assert "around line 400" in capsule
    # Identifier from the added lines (`_validate` is new in the gold patch).
    assert "`_validate`" in capsule
    # Guidance level must NOT include these.
    guidance = distill_gold_to_capsule(GOLD_PATCH, level="guidance")
    assert "around line" not in guidance
    assert "`_validate`" not in guidance


def test_distill_verbatim_marked_and_contains_patch() -> None:
    capsule = distill_gold_to_capsule(GOLD_PATCH, level="verbatim")
    assert "ORACLE-PROBE VERBATIM" in capsule
    assert "contaminated-by-design" in capsule
    # Verbatim deliberately includes the raw diff.
    assert "diff --git a/sphinx/util/docutils.py" in capsule


def test_distill_unknown_level_rejected() -> None:
    with pytest.raises(ValueError, match="unknown distillation level"):
        distill_gold_to_capsule(GOLD_PATCH, level="telepathy")


def test_outcome_variant_passthrough_to_rows(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path
    _make_scorecard(root, task_id, control_resolved=False, treatment_resolved=False)
    eval_report = _oracle_eval_report(tmp_path / "eval" / "report.json", task_id, resolved=False)
    out = root / "protocol_v2_oracle_probe" / task_id / "dose_detailed" / "outcome"
    result = write_oracle_probe_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=eval_report,
        output_dir=out,
        variant="dose_detailed",
    )
    assert result["report"]["variant"] == "dose_detailed"
    rows = load_oracle_probe_rows(root)
    assert [r["variant"] for r in rows] == ["dose_detailed"]


# ---------------------------------------------------------------------------
# build_oracle_probe_runtime_config
# ---------------------------------------------------------------------------


def _make_base_config(with_forbidden: bool = False) -> dict:
    config: dict = {
        "problem_statement": {"text": "Fix the bug in sphinx."},
        "output_dir": "/tmp/control_out",
        "agent": {"model": "gpt-4o"},
        "wutai_clinic": {"arm_type": "control"},
    }
    if with_forbidden:
        config["problem_statement"]["official_test_id"] = "FAIL_TO_PASS"
    return config


def test_build_config_injects_capsule() -> None:
    capsule = "ORACLE guidance here"
    cfg = build_oracle_probe_runtime_config(
        _make_base_config(),
        capsule_text=capsule,
        native_output_dir=Path("/tmp/oracle_out"),
    )
    assert capsule in cfg["problem_statement"]["text"]


def test_build_config_sets_arm_type() -> None:
    cfg = build_oracle_probe_runtime_config(
        _make_base_config(),
        capsule_text="hint",
        native_output_dir=Path("/tmp/out"),
    )
    assert cfg["wutai_clinic"]["arm_type"] == "oracle_treatment"


def test_build_config_contaminated_flags() -> None:
    cfg = build_oracle_probe_runtime_config(
        _make_base_config(),
        capsule_text="hint",
        native_output_dir=Path("/tmp/out"),
    )
    probe = cfg["wutai_clinic"]["oracle_probe"]
    assert probe["contaminated_by_design"] is True
    assert probe["oracle_derived"] is True
    assert probe["layer"] == ORACLE_PROBE_LAYER


def test_build_config_output_dir_redirected() -> None:
    cfg = build_oracle_probe_runtime_config(
        _make_base_config(),
        capsule_text="hint",
        native_output_dir=Path("/tmp/new_out"),
    )
    assert cfg["output_dir"] == "/tmp/new_out"


def test_build_config_raises_on_forbidden_token() -> None:
    # Config containing official_test_id → must raise.
    base = _make_base_config(with_forbidden=True)
    with pytest.raises(ValueError, match="official_test_id"):
        build_oracle_probe_runtime_config(
            base,
            capsule_text="hint",
            native_output_dir=Path("/tmp/out"),
        )


def test_build_config_raises_if_capsule_contains_fail_to_pass() -> None:
    with pytest.raises(ValueError, match="FAIL_TO_PASS"):
        build_oracle_probe_runtime_config(
            _make_base_config(),
            capsule_text="FAIL_TO_PASS test_func",
            native_output_dir=Path("/tmp/out"),
        )


def test_build_config_does_not_mutate_base() -> None:
    base = _make_base_config()
    original_text = base["problem_statement"]["text"]
    build_oracle_probe_runtime_config(
        base,
        capsule_text="oracle hint",
        native_output_dir=Path("/tmp/out"),
    )
    assert base["problem_statement"]["text"] == original_text
    assert base["wutai_clinic"]["arm_type"] == "control"


# ---------------------------------------------------------------------------
# write_oracle_probe_prepare_evidence
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _make_evidence_root_with_preflight(root: Path, task_id: str) -> None:
    config = {
        "problem_statement": {"text": f"Fix bug for {task_id}."},
        "output_dir": str(root / "native_out"),
        "agent": {"model": "gpt-4o"},
        "wutai_clinic": {"arm_type": "control", "instance_id": task_id},
    }
    _write_json(
        root
        / "protocol_v2_planned_preflight"
        / task_id
        / "control"
        / "protocol_v2_runtime_config.json",
        config,
    )


def test_write_prepare_blocked_missing_inputs(tmp_path: Path) -> None:
    result = write_oracle_probe_prepare_evidence(
        tmp_path / "missing_root",
        source_task_id="pkg__repo-1",
        output_dir=tmp_path / "out",
        gold_patches={},
    )
    assert result["report"]["decision"] == "oracle_probe_prepare_blocked_missing_inputs"
    assert result["report"]["passed"] is False


def test_write_prepare_full_tree(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    _make_evidence_root_with_preflight(root, task_id)
    gold_patches = {task_id: GOLD_PATCH}
    out = tmp_path / "oracle_prep"
    result = write_oracle_probe_prepare_evidence(
        root,
        source_task_id=task_id,
        output_dir=out,
        gold_patches=gold_patches,
    )
    assert result["report"]["decision"] == "oracle_probe_prepared_live_execution_not_authorized"
    assert result["report"]["passed"] is True
    manifest = json.loads(result["manifest_path"].read_text())
    assert manifest["oracle_derived"] is True
    assert manifest["contaminated_by_design"] is True
    # Config must not contain forbidden tokens.
    cfg_text = result["config_path"].read_text()
    assert "official_test_id" not in cfg_text
    assert "FAIL_TO_PASS" not in cfg_text
    # Capsule file written.
    assert result["capsule_path"].is_file()
    capsule_text = result["capsule_path"].read_text()
    assert "ORACLE-PROBE GUIDANCE" in capsule_text


def test_write_prepare_report_has_claim_boundary(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8435"
    root = tmp_path / "evidence"
    _make_evidence_root_with_preflight(root, task_id)
    result = write_oracle_probe_prepare_evidence(
        root,
        source_task_id=task_id,
        output_dir=tmp_path / "out",
        gold_patches={task_id: GOLD_PATCH},
    )
    assert result["report"]["claim_boundary"] == CLAIM_BOUNDARY


# ---------------------------------------------------------------------------
# write_oracle_probe_outcome_evidence
# ---------------------------------------------------------------------------


def _make_scorecard(
    root: Path, task_id: str, *, control_resolved: bool, treatment_resolved: bool
) -> None:
    _write_json(
        root / "protocol_v2_official_eval" / task_id / "protocol_v2_dual_scorecard.json",
        {
            "source_task_id": task_id,
            "control_resolved": control_resolved,
            "treatment_resolved": treatment_resolved,
        },
    )


def _oracle_eval_report(path: Path, task_id: str, *, resolved: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({task_id: {"resolved": resolved, "tests_status": {}}}) + "\n",
        encoding="utf-8",
    )
    return path


def test_write_outcome_oracle_resolves(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    _make_scorecard(root, task_id, control_resolved=False, treatment_resolved=False)
    oracle_report = _oracle_eval_report(
        tmp_path / "oracle_eval" / "report.json", task_id, resolved=True
    )
    result = write_oracle_probe_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=oracle_report,
        output_dir=tmp_path / "out",
    )
    assert result["report"]["decision"] == "oracle_probe_outcome_moved_channel_validated"
    three = result["report"]["three_arm_outcomes"]
    assert three["oracle_treatment_resolved"] is True
    assert three["control_resolved"] is False


def test_write_outcome_oracle_unresolved(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8435"
    root = tmp_path / "evidence"
    _make_scorecard(root, task_id, control_resolved=False, treatment_resolved=False)
    oracle_report = _oracle_eval_report(
        tmp_path / "oracle_eval" / "report.json", task_id, resolved=False
    )
    result = write_oracle_probe_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=oracle_report,
        output_dir=tmp_path / "out",
    )
    assert (
        result["report"]["decision"] == "oracle_probe_outcome_unmoved_channel_bottleneck_implicated"
    )
    three = result["report"]["three_arm_outcomes"]
    assert three["oracle_treatment_resolved"] is False


def test_write_outcome_missing_eval_blocked(tmp_path: Path) -> None:
    task_id = "pkg__repo-1"
    root = tmp_path / "evidence"
    _make_scorecard(root, task_id, control_resolved=False, treatment_resolved=False)
    result = write_oracle_probe_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=tmp_path / "nonexistent.json",
        output_dir=tmp_path / "out",
    )
    assert result["report"]["decision"] == "oracle_probe_outcome_blocked_missing_eval"


def test_write_outcome_manifest_oracle_flags(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    _make_scorecard(root, task_id, control_resolved=False, treatment_resolved=False)
    oracle_report = _oracle_eval_report(
        tmp_path / "oracle_eval" / "report.json", task_id, resolved=False
    )
    result = write_oracle_probe_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=oracle_report,
        output_dir=tmp_path / "out",
    )
    manifest = json.loads(result["manifest_path"].read_text())
    assert manifest["oracle_derived"] is True
    assert manifest["contaminated_by_design"] is True


# ---------------------------------------------------------------------------
# load_oracle_probe_rows
# ---------------------------------------------------------------------------


def test_load_oracle_probe_rows_empty(tmp_path: Path) -> None:
    assert load_oracle_probe_rows(tmp_path / "missing") == []


def test_load_oracle_probe_rows_scans(tmp_path: Path) -> None:
    # Live pipeline writes reports one level deeper (<target>/outcome/); the
    # loader must find both depths.
    for task_id, subdir in (
        ("sphinx-doc__sphinx-8474", ("outcome",)),
        ("sphinx-doc__sphinx-8435", ()),
    ):
        report = {
            "source_task_id": task_id,
            "decision": "oracle_probe_outcome_moved_channel_validated",
            "layer": ORACLE_PROBE_LAYER,
            "three_arm_outcomes": {"oracle_treatment_resolved": True},
        }
        _write_json(
            tmp_path.joinpath("protocol_v2_oracle_probe", task_id, *subdir)
            / "oracle_probe_outcome_report.json",
            report,
        )
    rows = load_oracle_probe_rows(tmp_path)
    assert len(rows) == 2
    assert all(r["contaminated_by_design"] is True for r in rows)
    assert all(r["layer"] == ORACLE_PROBE_LAYER for r in rows)
    assert all(r["oracle_treatment_resolved"] is True for r in rows)


# ---------------------------------------------------------------------------
# Task10: replay-free variant config + typing
# ---------------------------------------------------------------------------


def _oracle_probe_config() -> dict:
    return build_oracle_probe_runtime_config(
        _make_base_config(),
        capsule_text="oracle hint",
        native_output_dir=Path("/tmp/with_prefix/native"),
    )


def test_replay_free_variant_redirects_and_marks() -> None:
    variant = build_replay_free_variant_config(
        _oracle_probe_config(), native_output_dir=Path("/tmp/replay_free/native")
    )
    assert variant["output_dir"] == "/tmp/replay_free/native"
    probe = variant["wutai_clinic"]["oracle_probe"]
    assert probe["replay_prefix"] == "none"
    assert probe["variant"] == "replay_free"
    assert probe["contaminated_by_design"] is True


def test_replay_free_variant_rejects_non_probe_config() -> None:
    with pytest.raises(ValueError, match="not an oracle probe"):
        build_replay_free_variant_config(_make_base_config(), native_output_dir=Path("/tmp/out"))


def test_replay_free_variant_preserves_capsule() -> None:
    base = _oracle_probe_config()
    variant = build_replay_free_variant_config(base, native_output_dir=Path("/tmp/out"))
    assert variant["problem_statement"]["text"] == base["problem_statement"]["text"]


def test_classify_replay_free_moved() -> None:
    typing = classify_replay_free_probe(
        oracle_resolved=True, patch_text=CONTROL_PATCH_FOR_TYPING, gold_patch_text=GOLD_PATCH
    )
    assert typing["decision"] == "oracle_probe_replay_free_outcome_moved_prefix_lockin_implicated"


def test_classify_replay_free_capability_ceiling() -> None:
    # Patch nearly identical to gold (same file, tiny distance) but unresolved.
    near_gold_patch = GOLD_PATCH
    typing = classify_replay_free_probe(
        oracle_resolved=False, patch_text=near_gold_patch, gold_patch_text=GOLD_PATCH
    )
    assert typing["decision"] == "oracle_probe_replay_free_unmoved_capability_ceiling_implicated"
    assert typing["proximity"]["near_gold"] is True
    assert typing["proximity"]["gold_edit_distance"] == 0.0


def test_classify_replay_free_channel_capacity() -> None:
    far_patch = (
        "diff --git a/unrelated/file.py b/unrelated/file.py\n"
        "--- a/unrelated/file.py\n+++ b/unrelated/file.py\n"
        "@@ -1 +1 @@\n-zzz\n+qqq\n"
    )
    typing = classify_replay_free_probe(
        oracle_resolved=False, patch_text=far_patch, gold_patch_text=GOLD_PATCH
    )
    assert typing["decision"] == "oracle_probe_replay_free_unmoved_channel_capacity_implicated"
    assert typing["proximity"]["near_gold"] is False


def test_classify_replay_free_missing_eval() -> None:
    typing = classify_replay_free_probe(
        oracle_resolved=None, patch_text="x", gold_patch_text=GOLD_PATCH
    )
    assert typing["decision"] == "oracle_probe_replay_free_blocked_missing_eval"


def test_classify_replay_free_missing_gold() -> None:
    typing = classify_replay_free_probe(oracle_resolved=False, patch_text="x", gold_patch_text=None)
    assert typing["decision"] == "oracle_probe_replay_free_blocked_missing_patch_or_gold"
    assert typing["proximity"] is None


CONTROL_PATCH_FOR_TYPING = """\
diff --git a/sphinx/util/docutils.py b/sphinx/util/docutils.py
--- a/sphinx/util/docutils.py
+++ b/sphinx/util/docutils.py
@@ -1 +1 @@
-a
+b
"""


def test_write_replay_free_outcome_evidence(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    eval_report = _oracle_eval_report(tmp_path / "eval" / "report.json", task_id, resolved=False)
    patch_path = tmp_path / "rf.patch"
    patch_path.write_text(GOLD_PATCH, encoding="utf-8")
    out = tmp_path / "out"
    result = write_oracle_probe_replay_free_outcome_evidence(
        root,
        source_task_id=task_id,
        oracle_eval_report_path=eval_report,
        replay_free_patch_path=patch_path,
        gold_patches={task_id: GOLD_PATCH},
        output_dir=out,
    )
    report = result["report"]
    assert report["decision"] == "oracle_probe_replay_free_unmoved_capability_ceiling_implicated"
    assert report["variant"] == "replay_free"
    assert report["contaminated_by_design"] is True
    manifest = json.loads(result["manifest_path"].read_text())
    assert manifest["oracle_derived"] is True
    payload = json.loads(result["report_path"].read_text())
    assert payload["variant"] == "replay_free"


def test_load_rows_variant_passthrough(tmp_path: Path) -> None:
    _write_json(
        tmp_path
        / "protocol_v2_oracle_probe"
        / "inst-a"
        / "outcome"
        / "oracle_probe_outcome_report.json",
        {
            "source_task_id": "inst-a",
            "decision": "oracle_probe_outcome_unmoved_channel_bottleneck_implicated",
            "three_arm_outcomes": {"oracle_treatment_resolved": False},
        },
    )
    _write_json(
        tmp_path
        / "protocol_v2_oracle_probe"
        / "inst-a"
        / "replay_free"
        / "outcome"
        / "oracle_probe_outcome_report.json",
        {
            "source_task_id": "inst-a",
            "decision": "oracle_probe_replay_free_unmoved_capability_ceiling_implicated",
            "variant": "replay_free",
            "three_arm_outcomes": {"oracle_treatment_resolved": False},
        },
    )
    rows = load_oracle_probe_rows(tmp_path)
    assert len(rows) == 2
    variants = {row["variant"] for row in rows}
    assert variants == {"with_replay_prefix", "replay_free"}


# ---------------------------------------------------------------------------
# Batch outcomes excludes oracle layer
# ---------------------------------------------------------------------------


def test_batch_outcomes_oracle_exclusion(tmp_path: Path) -> None:
    from wutai_clinic.intervention.protocol_v2_batch_outcomes import (
        write_protocol_v2_batch_outcomes_evidence,
    )

    root = tmp_path / "evidence"
    # Write an oracle probe outcome report under the oracle probe dir.
    task_id = "sphinx-doc__sphinx-8474"
    _write_json(
        root / "protocol_v2_oracle_probe" / task_id / "oracle_probe_outcome_report.json",
        {
            "source_task_id": task_id,
            "decision": "oracle_probe_outcome_moved_channel_validated",
            "layer": ORACLE_PROBE_LAYER,
            "three_arm_outcomes": {"oracle_treatment_resolved": True},
        },
    )
    # Write minimal fresh candidate gate so function can find zero pairs.
    fresh = root / "protocol_v2_fresh_candidate_gate"
    fresh.mkdir(parents=True, exist_ok=True)
    (fresh / "protocol_v2_fresh_candidate_set_candidates.jsonl").write_text("", encoding="utf-8")

    result = write_protocol_v2_batch_outcomes_evidence(root=root, output_dir=tmp_path / "batch_out")
    report = result["report"]
    # Oracle probe rows must be listed in exclusions.
    assert report["summary"]["oracle_probe_pair_count_excluded"] == 1
    excluded = report["oracle_probe_rows_excluded"]
    assert len(excluded) == 1
    assert excluded[0]["contaminated_by_design"] is True


# ---------------------------------------------------------------------------
# CLI oracle-probe-prepare
# ---------------------------------------------------------------------------


def test_cli_oracle_probe_prepare_blocked_no_gold(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    _make_evidence_root_with_preflight(root, task_id)
    gold_jsonl = tmp_path / "gold.jsonl"
    # Write gold for a different instance → prepare should be blocked (gold missing)
    gold_jsonl.write_text(
        json.dumps({"instance_id": "other__pkg-9", "patch": GOLD_PATCH}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "prepare_out"
    result = runner.invoke(
        app,
        [
            "oracle-probe-prepare",
            str(root),
            "--source-task-id",
            task_id,
            "--offline-gold",
            str(gold_jsonl),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "oracle_probe_prepare_blocked_missing_inputs"


def test_cli_oracle_probe_prepare_full(tmp_path: Path) -> None:
    task_id = "sphinx-doc__sphinx-8474"
    root = tmp_path / "evidence"
    _make_evidence_root_with_preflight(root, task_id)
    gold_jsonl = tmp_path / "gold.jsonl"
    gold_jsonl.write_text(
        json.dumps({"instance_id": task_id, "patch": GOLD_PATCH}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "prepare_out"
    result = runner.invoke(
        app,
        [
            "oracle-probe-prepare",
            str(root),
            "--source-task-id",
            task_id,
            "--offline-gold",
            str(gold_jsonl),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "oracle_probe_prepared_live_execution_not_authorized"
    assert payload["contaminated_by_design"] is True
    # Config isolation invariant verified.
    cfg_text = Path(payload["config"]).read_text()
    assert "official_test_id" not in cfg_text
    manifest = json.loads((out / "oracle_probe_manifest.json").read_text())
    assert manifest["oracle_derived"] is True
