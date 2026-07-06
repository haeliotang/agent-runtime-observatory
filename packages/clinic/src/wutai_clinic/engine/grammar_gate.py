from __future__ import annotations

import json
from typing import Any

CANONICAL_TOOLS = {
    "run_command",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "view_file",
    "grep_search",
    "list_dir",
    "read_url_content",
    "read_browser_page",
    "code_synthesis",
    "continuous_action",
}

TOOL_REQUIRED_ARGS = {
    "run_command": ("command",),
    "view_file": ("path",),
    "list_dir": ("path",),
    "grep_search": ("pattern",),
    "write_to_file": ("path",),
    "replace_file_content": ("path", "old", "new"),
    "multi_replace_file_content": ("path",),
    "read_url_content": ("url",),
    "read_browser_page": ("url",),
    "continuous_action": ("payload",),
}

TEXT_ROUTE_PROBES = {
    "cognitive_steering",
    "defensive_secret_redaction",
    "val_loss_explanation",
    "safety_boundary",
}


def route_intent_for_probe(probe_id: str) -> str:
    return "text" if probe_id in TEXT_ROUTE_PROBES else "tool"


def parse_tool_call(response: str) -> tuple[bool, dict[str, Any] | None, list[str]]:
    stripped = response.strip()
    looks_like = (
        stripped.startswith("{")
        or '"type"' in stripped
        or '"tool_call"' in stripped
        or ('"name"' in stripped and '"arguments"' in stripped)
    )
    if not looks_like:
        return False, None, []

    candidates = [stripped]
    if "{" in stripped and "}" in stripped:
        candidates.append(stripped[stripped.find("{") : stripped.rfind("}") + 1])

    errors: list[str] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"json_parse:{exc.msg}")
            continue
        if isinstance(parsed, dict):
            return True, parsed, errors
        errors.append("json_not_object")
    return True, None, errors or ["json_parse_failed"]


def is_canonical_tool_json(text: str) -> bool:
    looks_like, data, _errors = parse_tool_call(text)
    if not looks_like or data is None:
        return False
    valid, _validation_errors = validate_tool_call(data)
    return valid


def classify_route(response: str) -> str:
    stripped = response.strip()
    if is_canonical_tool_json(stripped):
        return "tool"
    looks_like_tool, _parsed, _errors = parse_tool_call(stripped)
    return "ambiguous" if looks_like_tool else "text"


def validate_tool_call(
    tool_json: dict[str, Any], schema: dict[str, Any] | None = None
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if tool_json.get("type") != "tool_call":
        errors.append("missing_type_tool_call")
    name = tool_json.get("name")
    if not isinstance(name, str) or not name:
        errors.append("missing_tool_name")
    elif name not in CANONICAL_TOOLS:
        errors.append(f"invalid_tool_name:{name}")
    arguments = tool_json.get("arguments")
    if not isinstance(arguments, dict):
        errors.append("arguments_not_object")
    required = (schema or {}).get("required")
    if required is None and isinstance(name, str):
        required = TOOL_REQUIRED_ARGS.get(name, ())
    if required is None:
        required = ()
    if isinstance(arguments, dict):
        for field in required:
            if field not in arguments:
                errors.append(f"missing_arg:{field}")
    return not errors, errors


def validate_response_tool_call(response: str) -> tuple[bool, bool | None, str | None, list[str]]:
    looks_like, parsed, errors = parse_tool_call(response)
    if not looks_like:
        return False, None, None, []
    if parsed is None:
        return True, False, None, errors
    _valid, validation_errors = validate_tool_call(parsed)
    all_errors = [*errors, *validation_errors]
    name = parsed.get("name")
    return looks_like, not all_errors, name if isinstance(name, str) else None, all_errors


def canonical_tool_json(name: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {"type": "tool_call", "name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
    )
