"""Tests for wutai_clinic.reporting.html_report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


from wutai_clinic.reporting.html_report import (
    CLAIM_BANNER,
    build_html,
    collect_evidence_dag,
    collect_pair_outcomes,
    write_html_report,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_report(tmp_path: Path, subdir: str, filename: str, data: dict[str, Any]) -> Path:
    d = tmp_path / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _make_scorecard(tmp_path: Path, instance: str, effect_label: str) -> Path:
    return _make_report(
        tmp_path,
        f"protocol_v2_official_eval/{instance}",
        "protocol_v2_dual_scorecard.json",
        {
            "effect_label": effect_label,
            "pair_id": "pair_001",
            "source_task_id": instance,
            "decision": "ready",
        },
    )


# ── collect_pair_outcomes ─────────────────────────────────────────────────────


class TestCollectPairOutcomes:
    def test_valid_scorecard(self, tmp_path: Path) -> None:
        _make_scorecard(
            tmp_path, "myrepo__myrepo-1234", "both_unresolved_trigger_hit_pair_no_uplift"
        )
        outcomes = collect_pair_outcomes(tmp_path)
        found = [o for o in outcomes if o.get("instance_id") != "_unparsed"]
        assert len(found) == 1
        assert found[0]["effect_label"] == "both_unresolved_trigger_hit_pair_no_uplift"
        assert found[0]["instance_id"] == "myrepo__myrepo-1234"
        assert found[0]["pair_id"] == "pair_001"

    def test_missing_effect_label_key(self, tmp_path: Path) -> None:
        # JSON without effect_label should simply be skipped (not raise)
        d = tmp_path / "sub"
        d.mkdir()
        (d / "protocol_v2_dual_scorecard.json").write_text(
            json.dumps({"decision": "done", "pair_id": "x"}), encoding="utf-8"
        )
        outcomes = collect_pair_outcomes(tmp_path)
        found = [o for o in outcomes if o.get("instance_id") != "_unparsed"]
        assert len(found) == 0  # skipped, no raise

    def test_bad_json(self, tmp_path: Path) -> None:
        d = tmp_path / "sub"
        d.mkdir()
        (d / "protocol_v2_official_eval_report.json").write_text(
            "NOT VALID JSON {{{{", encoding="utf-8"
        )
        # Must not raise; bad file ends up in unparsed list
        outcomes = collect_pair_outcomes(tmp_path)
        unparsed = [o for o in outcomes if o.get("instance_id") == "_unparsed"]
        assert len(unparsed) == 1
        assert len(unparsed[0].get("_unparsed_files", [])) == 1

    def test_per_pair_nested(self, tmp_path: Path) -> None:
        """four_pair_official_eval_summary style: per_pair list."""
        _make_report(
            tmp_path,
            ".",
            "four_pair_official_eval_summary.json",
            {
                "decision": "done",
                "per_pair": [
                    {
                        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
                        "pair_id": "pair_013",
                        "source_task_id": "pytest-dev__pytest-8365",
                    },
                    {
                        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
                        "pair_id": "pair_016",
                        "source_task_id": "astropy__astropy-7746",
                    },
                ],
            },
        )
        outcomes = collect_pair_outcomes(tmp_path)
        found = [o for o in outcomes if o.get("instance_id") != "_unparsed"]
        assert len(found) == 2
        instance_ids = {o["instance_id"] for o in found}
        assert "pytest-dev__pytest-8365" in instance_ids

    def test_stratum_detection_v2(self, tmp_path: Path) -> None:
        _make_scorecard(
            tmp_path, "sphinx-doc__sphinx-8474", "both_unresolved_trigger_hit_pair_no_uplift"
        )
        outcomes = collect_pair_outcomes(tmp_path)
        found = [o for o in outcomes if o.get("instance_id") != "_unparsed"]
        assert found[0]["protocol_stratum"] == "v2_strict_fresh"


# ── build_html ────────────────────────────────────────────────────────────────


class TestBuildHtml:
    def _make_dag(self) -> dict[str, Any]:
        return {
            "nodes": [],
            "edges": [],
            "truncated": False,
            "truncated_count": 0,
            "scan_root": "/tmp",
        }

    def test_contains_claim_banner(self) -> None:
        html_out = build_html(pairs=[], dag=self._make_dag())
        assert "no new claims" in html_out
        assert CLAIM_BANNER[:40] in html_out

    def test_contains_effect_label_text(self) -> None:
        pairs = [
            {
                "protocol_stratum": "v0_reference",
                "instance_id": "pytest-dev__pytest-8365",
                "pair_id": "pair_013",
                "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
                "decision": "ready",
                "source_path": "/tmp/report.json",
            }
        ]
        html_out = build_html(pairs=pairs, dag=self._make_dag())
        assert "both_unresolved_trigger_hit_pair_no_uplift" in html_out

    def test_contains_svg(self) -> None:
        html_out = build_html(pairs=[], dag=self._make_dag())
        assert "<svg" in html_out

    def test_no_external_resources(self) -> None:
        html_out = build_html(pairs=[], dag=self._make_dag())
        assert 'src="http' not in html_out
        assert 'href="http' not in html_out

    def test_analysis_none_shows_not_provided(self) -> None:
        html_out = build_html(pairs=[], dag=self._make_dag(), analysis=None)
        assert "Not provided" in html_out

    def test_analysis_provided_shows_table(self) -> None:
        analysis = {"metrics": {"traj_1": {"avg_str": 0.75}}}
        html_out = build_html(pairs=[], dag=self._make_dag(), analysis=analysis)
        assert "avg_str" in html_out
        assert "traj_1" in html_out

    def test_unparsed_warning(self) -> None:
        pairs = [
            {
                "protocol_stratum": "unparsed",
                "instance_id": "_unparsed",
                "pair_id": "",
                "effect_label": "unparsed",
                "decision": "",
                "source_path": "",
                "_unparsed_files": [{"source_path": "/tmp/bad.json", "error": "bad json"}],
            }
        ]
        html_out = build_html(pairs=pairs, dag=self._make_dag())
        assert "could not be parsed" in html_out


# ── DAG truncation ────────────────────────────────────────────────────────────


class TestDagTruncation:
    def test_truncation_note_in_html(self, tmp_path: Path) -> None:
        """When DAG has >150 nodes, HTML must note truncation."""
        # Create 160 JSON files so DAG triggers truncation
        for i in range(160):
            sub = tmp_path / f"sub_{i}"
            sub.mkdir()
            # Use 'official_eval' in name so they pass pair-related filter
            (sub / f"official_eval_report_{i}.json").write_text(
                json.dumps(
                    {
                        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
                        "pair_id": f"pair_{i}",
                        "source_task_id": f"repo-{i}",
                    }
                ),
                encoding="utf-8",
            )

        dag = collect_evidence_dag(tmp_path)
        assert dag["truncated"] is True

        html_out = build_html(pairs=[], dag=dag)
        assert "TRUNCATED" in html_out or "omitted" in html_out

    def test_no_truncation_under_limit(self, tmp_path: Path) -> None:
        for i in range(10):
            sub = tmp_path / f"sub_{i}"
            sub.mkdir()
            (sub / f"protocol_v2_dual_scorecard_{i}.json").write_text(
                json.dumps(
                    {
                        "effect_label": "both_unresolved_trigger_hit_pair_no_uplift",
                        "pair_id": f"pair_{i}",
                        "source_task_id": f"repo-{i}",
                    }
                ),
                encoding="utf-8",
            )
        dag = collect_evidence_dag(tmp_path)
        assert dag["truncated"] is False


# ── write_html_report ─────────────────────────────────────────────────────────


class TestWriteHtmlReport:
    def test_writes_file(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        _make_scorecard(
            evidence, "myrepo__myrepo-9999", "both_unresolved_trigger_hit_pair_no_uplift"
        )

        output = tmp_path / "out" / "report.html"
        result = write_html_report(evidence, output)

        assert output.exists()
        assert result["pairs_found"] >= 1
        html_text = output.read_text(encoding="utf-8")
        assert "<svg" in html_text

    def test_no_external_links(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        output = tmp_path / "report.html"
        write_html_report(evidence, output)
        html_text = output.read_text(encoding="utf-8")
        assert 'src="http' not in html_text
        assert 'href="http' not in html_text

    def test_claim_text_present(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        output = tmp_path / "report.html"
        write_html_report(evidence, output)
        html_text = output.read_text(encoding="utf-8")
        assert "no new claims" in html_text

    def test_effect_label_present_when_data_exists(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        _make_scorecard(
            evidence, "sphinx-doc__sphinx-8474", "both_unresolved_trigger_hit_pair_no_uplift"
        )

        output = tmp_path / "report.html"
        write_html_report(evidence, output)
        html_text = output.read_text(encoding="utf-8")
        assert "both_unresolved_trigger_hit_pair_no_uplift" in html_text

    def test_analysis_path_missing_is_tolerated(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        output = tmp_path / "report.html"
        missing_analysis = tmp_path / "nonexistent.json"
        write_html_report(evidence, output, analysis_path=missing_analysis)
        assert output.exists()

    def test_result_dict_keys(self, tmp_path: Path) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        output = tmp_path / "report.html"
        result = write_html_report(evidence, output)
        for key in (
            "output_path",
            "pairs_found",
            "nodes_found",
            "edges_found",
            "truncated",
            "generated_at",
        ):
            assert key in result, f"Missing key: {key}"
