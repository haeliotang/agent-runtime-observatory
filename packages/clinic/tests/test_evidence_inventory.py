"""Tests for wutai_clinic.evidence.inventory module.

Synthetic fixtures in tmp_path only; no real model data accessed.
Covers: stratification, fault tolerance, manifest validation,
summary counts, fresh gate missing fallback.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wutai_clinic.evidence.inventory import (
    CLAIM_BOUNDARY,
    STATUS_MATERIALIZED_NOT_EXECUTED,
    STATUS_OFFICIAL_EVAL_COMPLETED,
    STATUS_UNPARSED,
    EvidenceIndexRow,
    _audit_manifest_hashes,
    _manifest_sha_entries,
    _sha256_file,
    build_index_summary,
    scan_evidence_root,
    write_evidence_index,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_sha(path: Path) -> str:
    return _sha256_file(path)


def _write_report(
    dirpath: Path,
    name: str,
    data: dict,
) -> Path:
    path = dirpath / name
    path.write_text(json.dumps(data, indent=2))
    return path


def _write_manifest(dirpath: Path, name: str, artifacts: list[dict]) -> Path:
    manifest_data = {
        "phase": "test",
        "decision": "ok",
        "passed": True,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "artifacts": artifacts,
    }
    path = dirpath / name
    path.write_text(json.dumps(manifest_data, indent=2))
    return path


# ---------------------------------------------------------------------------
# Unit tests for manifest helpers (extracted from cli.py)
# ---------------------------------------------------------------------------


class TestManifestHelpers:
    def test_sha256_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        digest = hashlib.sha256(b"hello world").hexdigest()
        assert _sha256_file(f) == digest

    def test_manifest_sha_entries_list_section(self) -> None:
        data = {
            "artifacts": [
                {"path": "foo.json", "sha256": "abc123"},
                {"sha256": "def456"},  # no path, gets index name
            ]
        }
        entries = _manifest_sha_entries(data)
        assert len(entries) == 2
        assert entries[0] == ("artifacts", "foo.json", {"path": "foo.json", "sha256": "abc123"})
        assert entries[1][0] == "artifacts"
        assert entries[1][1] == "artifacts[1]"

    def test_manifest_sha_entries_dict_section(self) -> None:
        data = {
            "outputs": {
                "report": {"path": "report.json", "sha256": "aabbcc"},
            }
        }
        entries = _manifest_sha_entries(data)
        assert len(entries) == 1
        assert entries[0][0] == "outputs"
        assert entries[0][1] == "report.json"

    def test_manifest_sha_entries_skips_no_sha(self) -> None:
        data = {"artifacts": [{"path": "foo.json"}]}  # no sha256 key
        entries = _manifest_sha_entries(data)
        assert entries == []

    def test_audit_manifest_hashes_pass(self, tmp_path: Path) -> None:
        f = tmp_path / "foo.json"
        f.write_text('{"ok": true}')
        sha = _sha256_file(f)
        manifest = _write_manifest(
            tmp_path,
            "test_manifest.json",
            [{"path": str(f), "sha256": sha}],
        )
        data = json.loads(manifest.read_text())
        result = _audit_manifest_hashes(manifest, tmp_path, data)
        assert result["hash_checked"] == 1
        assert result["hash_mismatch_count"] == 0
        assert result["hash_missing_count"] == 0

    def test_audit_manifest_hashes_mismatch(self, tmp_path: Path) -> None:
        f = tmp_path / "foo.json"
        f.write_text('{"ok": true}')
        manifest = _write_manifest(
            tmp_path,
            "test_manifest.json",
            [{"path": str(f), "sha256": "deadbeef" * 8}],
        )
        data = json.loads(manifest.read_text())
        result = _audit_manifest_hashes(manifest, tmp_path, data)
        assert result["hash_checked"] == 1
        assert result["hash_mismatch_count"] == 1


# ---------------------------------------------------------------------------
# Good pair fixture
# ---------------------------------------------------------------------------


def _make_good_pair(root: Path) -> Path:
    """Create a v0_reference style pair dir with report + valid manifest."""
    pair_dir = root / "astropy__astropy-7746"
    pair_dir.mkdir(parents=True)

    report_data = {
        "source_task_id": "astropy__astropy-7746",
        "pair_id": "phase312_pair_016_x",
        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
        "decision": "phase6_official_eval_outcome_label_ready",
        "official_eval_completed": True,
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    report_path = _write_report(pair_dir, "phase6_official_eval_report.json", report_data)

    sha = _sha256_file(report_path)
    _write_manifest(
        pair_dir,
        "phase6_official_eval_manifest.json",
        [{"path": report_path.as_posix(), "sha256": sha}],
    )
    return pair_dir


# ---------------------------------------------------------------------------
# Bad JSON fixture
# ---------------------------------------------------------------------------


def _make_bad_json_pair(root: Path) -> Path:
    """Create a pair dir with a malformed JSON report."""
    pair_dir = root / "broken__broken-0001"
    pair_dir.mkdir(parents=True)
    bad = pair_dir / "phase6_official_eval_report.json"
    bad.write_text("{not valid json!!!}")
    return pair_dir


# ---------------------------------------------------------------------------
# Missing manifest pair fixture
# ---------------------------------------------------------------------------


def _make_no_manifest_pair(root: Path) -> Path:
    """Create a pair dir with a valid report but no manifest."""
    pair_dir = root / "pytest-dev__pytest-8365"
    pair_dir.mkdir(parents=True)
    report_data = {
        "source_task_id": "pytest-dev__pytest-8365",
        "pair_id": "phase312_pair_019_x",
        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
        "decision": "phase6_official_eval_outcome_label_ready",
        "official_eval_completed": True,
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    _write_report(pair_dir, "phase6_official_eval_report.json", report_data)
    return pair_dir


# ---------------------------------------------------------------------------
# Materialized-not-executed fixture
# ---------------------------------------------------------------------------


def _make_materialized_not_executed(root: Path) -> Path:
    """Create a materialized-not-executed pair inputs dir."""
    mat_dir = root / "protocol_v2_fresh_state_capsule_pair_inputs"
    mat_dir.mkdir(parents=True)
    instance_dir = mat_dir / "sphinx-doc__sphinx-7686"
    instance_dir.mkdir()
    report_data = {
        "source_task_id": "sphinx-doc__sphinx-7686",
        "pair_id": "phase312_pair_021_x",
        "decision": "materialized_not_executed",
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    _write_report(instance_dir, "sphinx-doc__sphinx-7686_live_pair_inputs_report.json", report_data)
    return mat_dir


# ---------------------------------------------------------------------------
# Fresh gate fixture
# ---------------------------------------------------------------------------


def _make_fresh_gate_file(root: Path, ids: list[str]) -> Path:
    gate_dir = root / "protocol_v2_fresh_candidate_gate"
    gate_dir.mkdir(parents=True)
    gate_file = gate_dir / "protocol_v2_fresh_candidate_set_candidates.jsonl"
    lines = [json.dumps({"source_task_id": tid}) for tid in ids]
    gate_file.write_text("\n".join(lines) + "\n")
    return gate_file


# ---------------------------------------------------------------------------
# v2 pair fixtures
# ---------------------------------------------------------------------------


def _make_v2_pair(root: Path, instance_id: str, in_gate: bool) -> Path:
    """Create a protocol_v2_official_eval pair."""
    eval_dir = root / "protocol_v2_official_eval" / instance_id
    eval_dir.mkdir(parents=True)
    report_data = {
        "source_task_id": instance_id,
        "pair_id": f"pair_for_{instance_id}",
        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
        "decision": "protocol_v2_official_eval_outcome_label_ready",
        "official_eval_completed": True,
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    _write_report(eval_dir, "protocol_v2_official_eval_report.json", report_data)
    return eval_dir


# ---------------------------------------------------------------------------
# Tests: scan_evidence_root
# ---------------------------------------------------------------------------


class TestScanEvidenceRoot:
    def test_good_pair_found(self, tmp_path: Path) -> None:
        _make_good_pair(tmp_path)
        rows = scan_evidence_root(tmp_path)
        completed = [r for r in rows if r.status == STATUS_OFFICIAL_EVAL_COMPLETED]
        assert len(completed) >= 1
        ids = {r.instance_id for r in completed}
        assert "astropy__astropy-7746" in ids

    def test_bad_json_produces_unparsed_row(self, tmp_path: Path) -> None:
        _make_bad_json_pair(tmp_path)
        rows = scan_evidence_root(tmp_path)
        unparsed = [r for r in rows if r.status == STATUS_UNPARSED]
        assert len(unparsed) >= 1

    def test_no_manifest_pair_manifest_ok_none(self, tmp_path: Path) -> None:
        _make_no_manifest_pair(tmp_path)
        rows = scan_evidence_root(tmp_path)
        completed = [r for r in rows if r.instance_id == "pytest-dev__pytest-8365"]
        assert len(completed) == 1
        assert completed[0].manifest_ok is None
        assert completed[0].manifest_path == ""

    def test_manifest_validated_when_present(self, tmp_path: Path) -> None:
        _make_good_pair(tmp_path)
        rows = scan_evidence_root(tmp_path)
        good = [r for r in rows if r.instance_id == "astropy__astropy-7746"]
        assert len(good) == 1
        assert good[0].manifest_ok is True

    def test_materialized_not_executed_found(self, tmp_path: Path) -> None:
        _make_materialized_not_executed(tmp_path)
        rows = scan_evidence_root(tmp_path)
        mat = [r for r in rows if r.status == STATUS_MATERIALIZED_NOT_EXECUTED]
        assert len(mat) >= 1
        ids = {r.instance_id for r in mat}
        assert "sphinx-doc__sphinx-7686" in ids

    def test_materialized_not_executed_not_duplicated_when_also_completed(
        self, tmp_path: Path
    ) -> None:
        """If a task has a completed official eval AND appears in mat dir, it should not be mat."""
        # Create a completed pair
        completed_dir = tmp_path / "scipy__scipy-1234"
        completed_dir.mkdir()
        report_data = {
            "source_task_id": "scipy__scipy-1234",
            "pair_id": "pair_x",
            "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
            "decision": "protocol_v2_official_eval_outcome_label_ready",
            "official_eval_completed": True,
            "generated_at": "2026-01-01T00:00:00+00:00",
        }
        _write_report(completed_dir, "phase6_official_eval_report.json", report_data)
        # Also put it in a mat dir
        mat_dir = tmp_path / "protocol_v2_fresh_state_capsule_pair_inputs" / "scipy__scipy-1234"
        mat_dir.mkdir(parents=True)
        _write_report(mat_dir, "scipy__scipy-1234_live_pair_inputs_report.json", {"source_task_id": "scipy__scipy-1234"})
        rows = scan_evidence_root(tmp_path)
        completed = [r for r in rows if r.instance_id == "scipy__scipy-1234" and r.status == STATUS_OFFICIAL_EVAL_COMPLETED]
        mat = [r for r in rows if r.instance_id == "scipy__scipy-1234" and r.status == STATUS_MATERIALIZED_NOT_EXECUTED]
        assert len(completed) >= 1
        assert len(mat) == 0

    def test_fresh_gate_missing_produces_lineage_note(self, tmp_path: Path) -> None:
        """When fresh gate file is absent, v2 rows must have lineage_note set."""
        _make_v2_pair(tmp_path, "sphinx-doc__sphinx-8474", in_gate=True)
        # No gate file created
        rows = scan_evidence_root(tmp_path)
        v2_rows = [r for r in rows if r.instance_id == "sphinx-doc__sphinx-8474"]
        assert len(v2_rows) >= 1
        for row in v2_rows:
            assert row.lineage_note == "fresh_gate_listing_unavailable"

    def test_v2_stratification_with_gate(self, tmp_path: Path) -> None:
        """With gate file, sphinx-8474 in gate → v2_strict_fresh; sympy in gate absent → v2_reference."""
        _make_fresh_gate_file(tmp_path, ["sphinx-doc__sphinx-8474"])
        _make_v2_pair(tmp_path, "sphinx-doc__sphinx-8474", in_gate=True)
        _make_v2_pair(tmp_path, "sympy__sympy-16281", in_gate=False)
        rows = scan_evidence_root(tmp_path)
        sphinx = next(r for r in rows if r.instance_id == "sphinx-doc__sphinx-8474")
        sympy = next(r for r in rows if r.instance_id == "sympy__sympy-16281")
        assert sphinx.protocol_stratum == "v2_strict_fresh"
        assert sympy.protocol_stratum == "v2_reference"
        assert sphinx.lineage_note == ""
        assert sympy.lineage_note == ""

    def test_v0_reference_stratum(self, tmp_path: Path) -> None:
        _make_good_pair(tmp_path)
        rows = scan_evidence_root(tmp_path)
        astropy = next(r for r in rows if r.instance_id == "astropy__astropy-7746")
        assert astropy.protocol_stratum == "v0_reference"

    def test_scan_tolerates_mixed_content(self, tmp_path: Path) -> None:
        """All fixture types together: no exception raised."""
        _make_good_pair(tmp_path)
        _make_bad_json_pair(tmp_path)
        _make_no_manifest_pair(tmp_path)
        _make_materialized_not_executed(tmp_path)
        rows = scan_evidence_root(tmp_path)
        assert len(rows) >= 4
        statuses = {r.status for r in rows}
        assert STATUS_OFFICIAL_EVAL_COMPLETED in statuses
        assert STATUS_UNPARSED in statuses
        assert STATUS_MATERIALIZED_NOT_EXECUTED in statuses


# ---------------------------------------------------------------------------
# Tests: build_index_summary
# ---------------------------------------------------------------------------


class TestBuildIndexSummary:
    def _make_rows(self) -> list[EvidenceIndexRow]:
        def _row(**kwargs) -> EvidenceIndexRow:
            defaults = dict(
                instance_id="x",
                pair_id="p",
                protocol_stratum="v0_reference",
                effect_label="both_unresolved_trigger_hit_pair_no_uplift",
                trajectory_class="trajectory_diverged_no_uplift",
                status=STATUS_OFFICIAL_EVAL_COMPLETED,
                decision="ok",
                lineage_note="",
                report_path="/tmp/r.json",
                manifest_path="",
                manifest_ok=None,
                generated_at="",
            )
            defaults.update(kwargs)
            return EvidenceIndexRow(**defaults)

        return [
            _row(instance_id="a", protocol_stratum="v0_reference"),
            _row(instance_id="b", protocol_stratum="v1_fresh"),
            _row(
                instance_id="c",
                protocol_stratum="v2_strict_fresh",
                effect_label="positive_uplift_control_unresolved_treatment_resolved",
            ),
            _row(
                instance_id="d",
                protocol_stratum="v2_reference",
                status=STATUS_MATERIALIZED_NOT_EXECUTED,
                effect_label="",
            ),
            _row(
                instance_id="e",
                protocol_stratum="",
                status=STATUS_UNPARSED,
                effect_label="",
            ),
        ]

    def test_stratum_counts(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert s["stratum_counts"]["v0_reference"] == 1
        assert s["stratum_counts"]["v1_fresh"] == 1
        assert s["stratum_counts"]["v2_strict_fresh"] == 1
        assert s["stratum_counts"]["v2_reference"] == 1

    def test_uplift_count(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert s["uplift_pair_count"] == 1

    def test_no_uplift_count(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert s["uplift_pair_count"] == 1

    def test_materialized_not_executed_list(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert "d" in s["materialized_not_executed"]
        assert s["materialized_not_executed_count"] == 1

    def test_unparsed_count(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert s["unparsed_count"] == 1

    def test_total_rows(self) -> None:
        rows = self._make_rows()
        s = build_index_summary(rows)
        assert s["total_rows"] == 5

    def test_claim_boundary_present(self) -> None:
        s = build_index_summary([])
        assert s["claim_boundary"] == CLAIM_BOUNDARY

    def test_zero_uplift_when_no_positive_effects(self) -> None:
        def _no_uplift_row(iid: str) -> EvidenceIndexRow:
            return EvidenceIndexRow(
                instance_id=iid,
                pair_id="p",
                protocol_stratum="v0_reference",
                effect_label="both_unresolved_trigger_hit_pair_no_uplift",
                trajectory_class="",
                status=STATUS_OFFICIAL_EVAL_COMPLETED,
                decision="ok",
                lineage_note="",
                report_path="/tmp/r.json",
                manifest_path="",
                manifest_ok=None,
                generated_at="",
            )
        rows = [_no_uplift_row("a"), _no_uplift_row("b"), _no_uplift_row("c")]
        s = build_index_summary(rows)
        assert s["uplift_pair_count"] == 0


# ---------------------------------------------------------------------------
# Tests: write_evidence_index
# ---------------------------------------------------------------------------


class TestWriteEvidenceIndex:
    def test_writes_all_artifacts(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_good_pair(root)
        result = write_evidence_index(root, out)

        assert result["rows_path"].exists()
        assert result["report_path"].exists()
        assert result["manifest_path"].exists()

    def test_rows_jsonl_valid(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_good_pair(root)
        result = write_evidence_index(root, out)

        rows_raw = result["rows_path"].read_text().strip().splitlines()
        assert len(rows_raw) >= 1
        for line in rows_raw:
            obj = json.loads(line)
            assert "instance_id" in obj
            assert "status" in obj

    def test_report_json_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_good_pair(root)
        result = write_evidence_index(root, out)

        report = json.loads(result["report_path"].read_text())
        assert report["decision"] == "evidence_index_ready"
        assert report["passed"] is True
        assert "summary" in report
        assert "claim_boundary" in report
        assert report["claim_boundary"] == CLAIM_BOUNDARY

    def test_manifest_json_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_good_pair(root)
        result = write_evidence_index(root, out)

        manifest = json.loads(result["manifest_path"].read_text())
        assert "artifacts" in manifest
        assert "claim_boundary" in manifest
        sha_entries = _manifest_sha_entries(manifest)
        assert len(sha_entries) >= 1

    def test_summary_uplift_zero(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_good_pair(root)
        _make_no_manifest_pair(root)
        result = write_evidence_index(root, out)
        assert result["summary"]["uplift_pair_count"] == 0

    def test_materialized_not_executed_captured(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_materialized_not_executed(root)
        result = write_evidence_index(root, out)
        assert result["summary"]["materialized_not_executed_count"] >= 1

    def test_fault_tolerant_bad_json(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_bad_json_pair(root)
        # Should not raise
        result = write_evidence_index(root, out)
        assert result["summary"]["unparsed_count"] >= 1

    def test_fresh_gate_missing_note_in_rows(self, tmp_path: Path) -> None:
        root = tmp_path / "evidence"
        root.mkdir()
        out = tmp_path / "output"
        _make_v2_pair(root, "sphinx-doc__sphinx-8474", in_gate=False)
        # No gate file — all v2 rows must carry lineage_note
        result = write_evidence_index(root, out)
        rows_raw = result["rows_path"].read_text().strip().splitlines()
        v2_rows = [json.loads(line) for line in rows_raw if "sphinx-doc__sphinx-8474" in line]
        assert len(v2_rows) >= 1
        for row in v2_rows:
            assert row.get("lineage_note") == "fresh_gate_listing_unavailable"
