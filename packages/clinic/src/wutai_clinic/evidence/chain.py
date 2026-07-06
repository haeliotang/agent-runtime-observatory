from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Gate:
    name: str
    check: Callable[[dict[str, Any]], bool]
    description: str = ""


@dataclass
class EvidenceNode:
    phase_id: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    decision: str = ""
    node_id: str = ""
    upstream: list[str] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.node_id or self.phase_id


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceChain:
    def __init__(self, nodes: list[EvidenceNode]) -> None:
        self.nodes = nodes
        self._by_key = {node.key: node for node in nodes}
        self._last_reports: dict[str, dict[str, Any]] = {}

    def topological_order(self) -> list[EvidenceNode]:
        ordered: list[EvidenceNode] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(node: EvidenceNode) -> None:
            if node.key in permanent:
                return
            if node.key in temporary:
                raise ValueError(f"Evidence cycle detected at {node.key}")
            temporary.add(node.key)
            for upstream_key in node.upstream:
                if upstream_key not in self._by_key:
                    raise KeyError(f"Unknown upstream evidence node: {upstream_key}")
                visit(self._by_key[upstream_key])
            temporary.remove(node.key)
            permanent.add(node.key)
            ordered.append(node)

        for node in self.nodes:
            visit(node)
        return ordered

    def run_gates(self, node: EvidenceNode, context: dict[str, Any]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for gate in node.gates:
            try:
                results[gate.name] = bool(gate.check(context))
            except Exception:
                results[gate.name] = False
        return results

    def verify_upstream(self, node: EvidenceNode) -> bool:
        for raw_path in node.inputs:
            path = Path(raw_path)
            if not path.exists():
                return False
            expected = node.input_hashes.get(raw_path) or node.input_hashes.get(path.as_posix())
            if expected and sha256_file(path) != expected:
                return False
        for upstream_key in node.upstream:
            report = self._last_reports.get(upstream_key)
            if report is not None and report.get("passed") is not True:
                return False
        return True

    def run_node(self, node: EvidenceNode, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        upstream_passed = self.verify_upstream(node)
        gate_results = self.run_gates(node, context)
        if node.inputs or node.upstream:
            gate_results = {"upstream": upstream_passed, **gate_results}
        report = self.emit_report(node, gate_results)
        self._last_reports[node.key] = report
        return report

    def run_all(
        self, context_by_node: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, dict[str, Any]]:
        reports = {}
        context_by_node = context_by_node or {}
        for node in self.topological_order():
            reports[node.key] = self.run_node(node, context_by_node.get(node.key, {}))
        return reports

    def emit_report(self, node: EvidenceNode, gate_results: dict[str, bool]) -> dict[str, Any]:
        blocking = [name for name, passed in gate_results.items() if not passed]
        return {
            "phase": node.phase_id,
            "node_id": node.key,
            "decision": node.decision,
            "inputs": node.inputs,
            "outputs": node.outputs,
            "gates": gate_results,
            "passed": not blocking,
            "blocking_failures": blocking,
        }
