from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

RAW_PAYLOAD_KEYS = {
    "action",
    "content",
    "gated_response",
    "history",
    "messages",
    "observation",
    "prompt",
    "query",
    "raw_response",
    "response",
    "system_prompt",
    "thought",
    "trajectory",
}
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9_.-]{8,}"),
)
GateFunction = Callable[[dict[str, Any]], bool]


def _contains_raw_payload(value: Any) -> bool:
    if isinstance(value, dict):
        if RAW_PAYLOAD_KEYS.intersection(value):
            return True
        return any(_contains_raw_payload(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_payload(item) for item in value)
    return False


def _contains_secret_literal(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_secret_literal(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_literal(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    return False


def no_raw_payload(context: dict[str, Any]) -> bool:
    return not _contains_raw_payload(context)


def no_secret_literal(context: dict[str, Any]) -> bool:
    return not _contains_secret_literal(context)


def sha256_match(context: dict[str, Any]) -> bool:
    path = Path(str(context.get("path", "")))
    expected = str(context.get("sha256", ""))
    if not path.exists() or not expected:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == expected


def count_match(context: dict[str, Any]) -> bool:
    expected = context.get("expected_count")
    actual = context.get("actual_count")
    return expected is not None and actual is not None and int(expected) == int(actual)


def decision_boundary(context: dict[str, Any]) -> bool:
    return bool(context.get("claim_boundary") or context.get("decision"))


STANDARD_GATES: dict[str, GateFunction] = {
    "no_raw_payload": no_raw_payload,
    "no_secret_literal": no_secret_literal,
    "sha256_match": sha256_match,
    "count_match": count_match,
    "decision_boundary": decision_boundary,
}


def get_gate(name: str) -> GateFunction:
    return STANDARD_GATES[name]


def register_gate(name: str, gate: GateFunction) -> None:
    STANDARD_GATES[name] = gate
