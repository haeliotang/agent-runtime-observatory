from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2 import ProtocolV2, protocol_v2_prescription_template

runner = CliRunner()


def test_protocol_v2_template_round_trips() -> None:
    protocol = protocol_v2_prescription_template()
    payload = protocol.to_dict()
    restored = ProtocolV2.from_dict(payload)

    assert restored.protocol_hash == protocol.protocol_hash
    assert payload["version"] == "protocol_v2_prescription"
    assert payload["action"]["prescription_id"] == "break_recurrence_and_reproduce"
    assert payload["guard"]["same_pair_positive_claim_allowed"] is False
    assert payload["claim"]["allowed"] == "prospective_batch_prescription_no_outcome_oracle"


def test_protocol_v2_rejects_official_eval_oracles_in_runtime_trigger() -> None:
    payload = protocol_v2_prescription_template().to_dict()
    payload["trigger"]["predicates"] = [
        "official_eval_resolved is false",
        "error_streak >= 2",
    ]

    with pytest.raises(ValueError, match="forbids official outcome/test oracles"):
        ProtocolV2.from_dict(payload)


def test_protocol_v2_rejects_executable_fields() -> None:
    payload = protocol_v2_prescription_template().to_dict()
    payload["action"]["python"] = "print('do not run')"

    with pytest.raises(ValueError, match="forbids executable fields"):
        ProtocolV2.from_dict(payload)


def test_cli_protocol_v2_prescription_template_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "protocol_v2_template.json"
    result = runner.invoke(
        app,
        [
            "protocol-v2-prescription-template",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    saved = json.loads(output.read_text())
    assert payload["decision"] == "protocol_v2_prescription_template_ready_not_live_executed"
    assert saved["decision"] == "protocol_v2_prescription_template_ready_not_live_executed"
    assert saved["protocol_v2"]["version"] == "protocol_v2_prescription"
