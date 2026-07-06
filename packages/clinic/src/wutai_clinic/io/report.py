from __future__ import annotations

import hashlib
from datetime import datetime, timezone

UTC = timezone.utc  # py3.10 compat: datetime.UTC is 3.11+
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_count(path: str | Path) -> int | None:
    target = Path(path)
    if target.suffix != ".jsonl" or not target.is_file():
        return None
    with target.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _artifact_entry(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    return {
        "path": target.as_posix(),
        "sha256": sha256_file(target) if target.is_file() else None,
        "record_count": _record_count(target),
    }


def generate_report(
    *,
    phase: str | None = None,
    decision: str | None = None,
    gate_results: dict[str, bool] | None = None,
    extras: dict[str, Any] | None = None,
    node: Any | None = None,
) -> dict[str, Any]:
    if node is not None:
        phase = node.phase_id
        decision = decision if decision is not None else node.decision
    gates = dict(gate_results or {})
    blocking_failures = [name for name, passed in gates.items() if not passed]
    report = {
        "phase": phase or "",
        "decision": decision or "",
        "generated_at": utc_now(),
        "gates": gates,
        "passed": not blocking_failures,
        "blocking_failures": blocking_failures,
    }
    if node is not None:
        report.update(
            {
                "node_id": node.key,
                "inputs": list(node.inputs),
                "outputs": list(node.outputs),
            }
        )
    if extras:
        report.update(extras)
    return report


def generate_manifest(
    *,
    phase: str | None = None,
    report: dict[str, Any],
    artifacts: list[dict[str, Any]] | None = None,
    node: Any | None = None,
) -> dict[str, Any]:
    if node is not None:
        phase = node.phase_id
        artifacts = [_artifact_entry(path) for path in [*node.inputs, *node.outputs]]
    return {
        "phase": phase or "",
        "generated_at": utc_now(),
        "decision": report.get("decision", ""),
        "passed": report.get("passed"),
        "artifacts": list(artifacts or []),
    }
