from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.engine.post_repair_outcomes import (
    CLAIM_BOUNDARY,
    REPAIRED_LINEAGE,
    assemble_post_repair,
    build_pre_repair_delta,
    write_post_repair_outcomes_evidence,
)

runner = CliRunner()


def _rows() -> list[dict]:
    return [
        {"instance_id": "sphinx-doc__sphinx-7686", "arm": "control", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-7686", "arm": "treatment", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "control", "resolved": True},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "treatment", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "oracle_prefix", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "epsilon_run_1", "resolved": True},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "epsilon_run_2", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-8435", "arm": "epsilon_run_3", "resolved": True},
        {"instance_id": "sphinx-doc__sphinx-8474", "arm": "control", "resolved": True},
        {"instance_id": "sphinx-doc__sphinx-8474", "arm": "treatment", "resolved": False},
        {"instance_id": "sphinx-doc__sphinx-8474", "arm": "dose_verbatim", "resolved": True},
    ]


def _make_root(root: Path, *, rows: list[dict] | None = None, all_valid: bool = True) -> None:
    reeval = root / "protocol_v2_substrate_repair_reeval"
    reeval.mkdir(parents=True, exist_ok=True)
    (reeval / "reeval_outcomes.json").write_text(
        json.dumps(rows if rows is not None else _rows()) + "\n", encoding="utf-8"
    )
    validity = {
        "decision": (
            "instance_validity_all_checked_instances_valid"
            if all_valid
            else "instance_validity_substrate_invalid_instances_found"
        ),
        "valid_instances": ["x"],
        "invalid_instances": [] if all_valid else ["y"],
    }
    iv = root / "instance_validity"
    iv.mkdir(parents=True, exist_ok=True)
    (iv / "instance_validity_report.json").write_text(json.dumps(validity) + "\n", encoding="utf-8")


def test_assemble_pairs_and_labels() -> None:
    assembled = assemble_post_repair(_rows())
    labels = {r["source_task_id"]: r["effect_label"] for r in assembled["pair_rows"]}
    assert labels["sphinx-doc__sphinx-7686"] == "both_unresolved_trigger_hit_pair_no_uplift"
    assert (
        labels["sphinx-doc__sphinx-8435"] == "control_only_resolved_trigger_hit_negative_candidate"
    )
    assert (
        labels["sphinx-doc__sphinx-8474"] == "control_only_resolved_trigger_hit_negative_candidate"
    )
    assert assembled["harm_pair_count"] == 2
    assert all(r["lineage"] == REPAIRED_LINEAGE for r in assembled["pair_rows"])


def test_assemble_epsilon_reference_is_repaired_control() -> None:
    assembled = assemble_post_repair(_rows())
    est = assembled["epsilon_estimates"]["sphinx-doc__sphinx-8435"]
    # Reference = repaired control (True); outcomes T/F/T -> 1 flip of 3.
    assert est["rerun_count"] == 3
    assert est["flip_count"] == 1
    assert est["reference_outcome"] is True


def test_assemble_probe_arms_excluded() -> None:
    assembled = assemble_post_repair(_rows())
    probes = assembled["probe_arms_excluded_from_stats"]
    assert probes["sphinx-doc__sphinx-8435"]["oracle_prefix"] is False
    assert probes["sphinx-doc__sphinx-8474"]["dose_verbatim"] is True
    # Probe arms never appear in pair rows.
    for row in assembled["pair_rows"]:
        assert "oracle" not in row["effect_label"]


def test_delta_records_harm_flip_and_epsilon() -> None:
    assembled = assemble_post_repair(_rows())
    deltas = build_pre_repair_delta(assembled)
    kinds = {(d["source_task_id"], d["post_repair"][:7]) for d in deltas}
    assert ("sphinx-doc__sphinx-8435", "control") in kinds
    assert ("sphinx-doc__sphinx-8474", "control") in kinds
    assert any(d["pre_repair"].startswith("epsilon") for d in deltas)


def test_write_evidence_harm_decision(tmp_path: Path) -> None:
    _make_root(tmp_path)
    result = write_post_repair_outcomes_evidence(tmp_path, tmp_path / "out")
    report = result["report"]
    assert report["decision"] == "post_repair_outcomes_harm_direction_on_valid_substrate"
    assert report["passed"] is True
    assert report["claim_boundary"] == CLAIM_BOUNDARY
    assert report["harm_pair_count"] == 2
    assert report["continuation_policy"]["allow_generalized_harm_claim"] is False
    assert report["continuation_policy"]["keep_prescription_frozen"] is True
    # No positive-uplift wording in claim boundary.
    lowered = report["claim_boundary"].lower()
    assert (
        lowered.count("uplift") == lowered.count("no uplift") + lowered.count("no-uplift")
        or "uplift" not in lowered
    )


def test_write_evidence_blocked_when_incomplete(tmp_path: Path) -> None:
    rows = _rows()
    rows[0]["resolved"] = None
    _make_root(tmp_path, rows=rows)
    result = write_post_repair_outcomes_evidence(tmp_path, tmp_path / "out")
    assert result["report"]["decision"] == "post_repair_outcomes_blocked_missing_inputs"


def test_write_evidence_blocked_when_validity_not_all_valid(tmp_path: Path) -> None:
    _make_root(tmp_path, all_valid=False)
    result = write_post_repair_outcomes_evidence(tmp_path, tmp_path / "out")
    assert result["report"]["decision"] == "post_repair_outcomes_blocked_missing_inputs"


def test_write_evidence_no_direction_change(tmp_path: Path) -> None:
    rows = [
        {"instance_id": "a__b-1", "arm": "control", "resolved": False},
        {"instance_id": "a__b-1", "arm": "treatment", "resolved": False},
    ]
    _make_root(tmp_path, rows=rows)
    result = write_post_repair_outcomes_evidence(tmp_path, tmp_path / "out")
    assert result["report"]["decision"] == "post_repair_outcomes_no_direction_change"


def test_cli_post_repair_outcomes(tmp_path: Path) -> None:
    _make_root(tmp_path)
    out = tmp_path / "out"
    result = runner.invoke(app, ["post-repair-outcomes", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["harm_pair_count"] == 2
    assert sorted(payload["harm_pair_ids"]) == [
        "sphinx-doc__sphinx-8435",
        "sphinx-doc__sphinx-8474",
    ]
    assert (out / "post_repair_outcomes_report.json").is_file()
    assert (out / "post_repair_outcomes_manifest.json").is_file()
