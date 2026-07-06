from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_b1 import ProtocolB1, protocol_b1_template

runner = CliRunner()


def _prereg_manifest() -> dict:
    return {
        "status": "route_b1_probe_preregistered_live_execution_not_authorized",
        "design": {
            "anchors": ["pallets__flask-4045", "sphinx-doc__sphinx-8474", "sphinx-doc__sphinx-7686"],
            "k_reps_per_arm": 5,
        },
        "live_authorization": {"authorized": False},
    }


def test_protocol_b1_template_round_trips() -> None:
    protocol = protocol_b1_template()
    restored = ProtocolB1.from_dict(protocol.to_dict())
    assert restored.protocol_hash == protocol.protocol_hash
    assert protocol.action.type == "inject_deployable_information"
    # Amendment A: issue-text-only reproduction.
    assert protocol.version == "protocol_b1_issue_text_repro"
    assert protocol.action.info_kind == "issue_text_reproduction"
    assert protocol.action.payload_provenance == "issue_text_only"
    assert protocol.guard.replay_free is True
    assert protocol.guard.oracle_capsule_allowed is False
    assert "official_test_identity" in protocol.guard.forbidden_payload_categories
    assert protocol.claim.allowed == "route_b_go_no_go_no_uplift_claim"


def test_protocol_b1_old_leaking_repro_form_is_inexpressible() -> None:
    # Amendment A: the pre-amendment form (run the failing test = FAIL_TO_PASS and
    # inject its traceback) leaked the benchmark oracle. It must no longer be
    # constructible in code.
    payload = protocol_b1_template().to_dict()
    payload["action"]["info_kind"] = "reproduction_first"
    payload["action"]["payload_fields"] = ["reproduction_traceback", "failing_test_assertion_expected"]
    with pytest.raises(ValueError, match="unknown Protocol B1 info_kind"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_rejects_non_issue_text_provenance() -> None:
    payload = protocol_b1_template().to_dict()
    payload["action"]["payload_provenance"] = "official_test_derived"
    with pytest.raises(ValueError, match="payload_provenance=issue_text_only"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_rejects_official_test_identity_token_in_trigger() -> None:
    payload = protocol_b1_template().to_dict()
    payload["trigger"]["predicates"] = ["fail_to_pass test reached is true", "about_to_emit_patch is true"]
    with pytest.raises(ValueError, match="oracle/answer tokens"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_rejects_oracle_capsule() -> None:
    payload = protocol_b1_template().to_dict()
    payload["guard"]["oracle_capsule_allowed"] = True
    with pytest.raises(ValueError, match="forbids oracle capsules"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_rejects_non_deployable_payload_field() -> None:
    payload = protocol_b1_template().to_dict()
    payload["action"]["payload_fields"] = ["gold_patch"]
    with pytest.raises(ValueError, match="deployable whitelist"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_rejects_uplift_claim_allowed() -> None:
    payload = protocol_b1_template().to_dict()
    payload["guard"]["uplift_claim_allowed"] = True
    with pytest.raises(ValueError, match="go/no-go only"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_requires_replay_free() -> None:
    payload = protocol_b1_template().to_dict()
    payload["guard"]["replay_free"] = False
    with pytest.raises(ValueError, match="replay_free"):
        ProtocolB1.from_dict(payload)


def test_protocol_b1_guard_must_cover_required_forbidden_categories() -> None:
    payload = protocol_b1_template().to_dict()
    payload["guard"]["forbidden_payload_categories"] = ["gold_patch"]  # missing the rest
    with pytest.raises(ValueError, match="must forbid leakage categories"):
        ProtocolB1.from_dict(payload)


def test_route_b1_plan_then_antileak_offline(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg_manifest.json"
    prereg_path.write_text(json.dumps(_prereg_manifest()), encoding="utf-8")
    plan_dir = tmp_path / "plan"
    antileak_dir = tmp_path / "antileak"

    plan_res = runner.invoke(app, ["route-b1-plan", str(prereg_path), "-o", str(plan_dir)])
    assert plan_res.exit_code == 0, plan_res.output
    plan_out = json.loads(plan_res.output)
    assert plan_out["decision"] == "route_b1_plan_ready_live_execution_not_authorized"
    assert plan_out["passed"] is True
    # 3 anchors x 2 arms x k=5 = 30 cells, live not authorized
    assert plan_out["summary"]["cells"] == 30
    assert plan_out["summary"]["live_execution_authorized"] is False
    assert (plan_dir / "protocol_b1.json").is_file()
    assert (plan_dir / "b1_anchor_plan.jsonl").is_file()

    leak_res = runner.invoke(app, ["route-b1-antileak", str(plan_dir), "-o", str(antileak_dir)])
    assert leak_res.exit_code == 0, leak_res.output
    leak_out = json.loads(leak_res.output)
    assert leak_out["decision"] == "route_b1_antileak_passed_payload_contract_clean"
    assert leak_out["passed"] is True
    # content-level diff is a live-time gate, deferred
    assert leak_out["content_diff_stage"] == "deferred_to_live_capture"


def test_route_b1_plan_blocks_when_prereg_authorizes_live(tmp_path: Path) -> None:
    manifest = _prereg_manifest()
    manifest["live_authorization"]["authorized"] = True  # must never be pre-authorized
    prereg_path = tmp_path / "prereg_manifest.json"
    prereg_path.write_text(json.dumps(manifest), encoding="utf-8")

    res = runner.invoke(app, ["route-b1-plan", str(prereg_path), "-o", str(tmp_path / "plan")])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["decision"] == "route_b1_plan_blocked"
    assert out["passed"] is False
    assert "prereg_blocks_live_authorization" in out["blocking_failures"]
