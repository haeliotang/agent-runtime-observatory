from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.evidence.chain import EvidenceChain, EvidenceNode, Gate, sha256_file
from wutai_clinic.evidence.registry import (
    count_match,
    decision_boundary,
    get_gate,
    no_raw_payload,
    no_secret_literal,
    register_gate,
)
from wutai_clinic.io.report import generate_manifest, generate_report

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_three_node_chain_propagates_pass_and_fail(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text('{"ok": true}\n', encoding="utf-8")
    digest = sha256_file(artifact)
    node_a = EvidenceNode(
        phase_id="A",
        node_id="a",
        outputs=[artifact.as_posix()],
        gates=[Gate("source_ready", lambda ctx: ctx["ready"])],
        decision="a_ready",
    )
    node_b = EvidenceNode(
        phase_id="B",
        node_id="b",
        inputs=[artifact.as_posix()],
        upstream=["a"],
        input_hashes={artifact.as_posix(): digest},
        gates=[Gate("row_count", lambda ctx: ctx["rows"] == 1)],
        decision="b_ready",
    )
    node_c = EvidenceNode(
        phase_id="C",
        node_id="c",
        upstream=["b"],
        gates=[Gate("final_gate", lambda ctx: False)],
        decision="c_blocked",
    )
    reports = EvidenceChain([node_c, node_b, node_a]).run_all(
        {"a": {"ready": True}, "b": {"rows": 1}, "c": {}}
    )

    assert reports["a"]["passed"] is True
    assert reports["b"]["gates"]["upstream"] is True
    assert reports["b"]["passed"] is True
    assert reports["c"]["passed"] is False
    assert reports["c"]["blocking_failures"] == ["final_gate"]


def test_upstream_hash_mismatch_blocks_node(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text("original", encoding="utf-8")
    node = EvidenceNode(
        phase_id="hash",
        node_id="hash",
        inputs=[artifact.as_posix()],
        input_hashes={artifact.as_posix(): "bad"},
        gates=[Gate("local", lambda ctx: True)],
    )

    report = EvidenceChain([node]).run_node(node)

    assert report["gates"]["upstream"] is False
    assert report["passed"] is False


def test_standard_gates_rebuild_phase310_evidence_boundary() -> None:
    report = json.loads((MODELS / "phase310_str_early_warning_pilot_report.json").read_text())

    assert no_raw_payload(report)
    assert no_secret_literal(report)
    assert decision_boundary(report)
    assert count_match({"expected_count": 3, "actual_count": 3})
    assert get_gate("decision_boundary")(report)


def test_secret_scanner_detects_keys_without_flagging_flask_paths() -> None:
    assert not no_secret_literal({"api_key": "sk-abcdefghijklmnopqrstuvwxyz"})
    assert no_secret_literal({"path": "pallets__flask-4045_run_single_config.json"})


def test_custom_gate_registration() -> None:
    register_gate("always_true_for_test", lambda ctx: ctx.get("ok") is True)

    assert get_gate("always_true_for_test")({"ok": True})


def test_generate_report_and_manifest_are_legacy_compatible(tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    output.write_text('{"x": 1}\n', encoding="utf-8")
    node = EvidenceNode(
        phase_id="4.1", node_id="node", outputs=[output.as_posix()], decision="ready"
    )

    report = generate_report(node=node, gate_results={"gate": True})
    manifest = generate_manifest(node=node, report=report)

    assert report["phase"] == "4.1"
    assert report["decision"] == "ready"
    assert report["passed"] is True
    assert report["gates"] == {"gate": True}
    assert manifest["phase"] == "4.1"
    assert manifest["decision"] == "ready"
    assert manifest["artifacts"][0]["sha256"] == sha256_file(output)
    assert manifest["artifacts"][0]["record_count"] == 1
