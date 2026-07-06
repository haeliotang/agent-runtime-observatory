from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from wutai_clinic.engine.grammar_gate import (
    canonical_tool_json,
    classify_route,
    is_canonical_tool_json,
    parse_tool_call,
    route_intent_for_probe,
    validate_response_tool_call,
    validate_tool_call,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_GATE = PACKAGE_ROOT.parent / "models/tool_grammar_gate.py"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_intent_matches_legacy_probe_table() -> None:
    legacy = _load_module(LEGACY_GATE)
    for probe_id in [
        "val_loss_explanation",
        "defensive_secret_redaction",
        "tool_file_creation",
        "error_recovery",
        "safety_boundary",
    ]:
        assert route_intent_for_probe(probe_id) == legacy.route_intent_for_probe(probe_id)


def test_parse_and_validate_response_matches_legacy_gate() -> None:
    legacy = _load_module(LEGACY_GATE)
    responses = [
        "plain text answer",
        canonical_tool_json("run_command", {"command": "python3 /tmp/wutai_eval/fib.py"}),
        json.dumps({"type": "tool_call", "name": "run_command", "arguments": {}}),
        json.dumps({"type": "tool_call", "name": "unknown_tool", "arguments": {}}),
        'prefix {"type":"tool_call","name":"view_file","arguments":{"path":"x.py"}} suffix',
        "{not-json",
    ]
    for response in responses:
        assert parse_tool_call(response) == legacy.parse_tool_call(response)
        assert validate_response_tool_call(response) == legacy.validate_tool_call(response)


def test_validate_tool_call_default_required_args_and_schema_override() -> None:
    valid = {
        "type": "tool_call",
        "name": "replace_file_content",
        "arguments": {"path": "x", "old": "a", "new": "b"},
    }
    assert validate_tool_call(valid) == (True, [])
    missing = {"type": "tool_call", "name": "replace_file_content", "arguments": {"path": "x"}}
    assert validate_tool_call(missing) == (False, ["missing_arg:old", "missing_arg:new"])
    assert validate_tool_call(missing, {"required": ["path"]}) == (True, [])


def test_classify_route_and_canonical_detection() -> None:
    assert classify_route("plain answer") == "text"
    valid = canonical_tool_json("grep_search", {"pattern": "phase3a38"})
    assert classify_route(valid) == "tool"
    assert is_canonical_tool_json(valid)
    invalid = json.dumps({"type": "tool_call", "name": "grep_search", "arguments": {}})
    assert classify_route(invalid) == "ambiguous"
    assert not is_canonical_tool_json(invalid)
