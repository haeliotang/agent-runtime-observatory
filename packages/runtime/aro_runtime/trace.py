"""JSONL trace files: the durable, inspectable record of a run.

One event per line. The trace header embeds the full script and policy bundle
so a trace is self-describing: replay needs only the trace file plus the
workspace it was recorded against (verified by digest).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from aro_schema import (
    AgentRun,
    Artifact,
    Coverage,
    EvidenceItem,
    PolicyDecision,
    RiskSignal,
    RunStatus,
    StepRecord,
)

TRACE_VERSION = 1


class TraceWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self.path.open("w")

    def event(self, type_: str, **payload: Any) -> None:
        self._fh.write(json.dumps({"type": type_, **payload}, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def load_trace(path: Path) -> tuple[dict, AgentRun]:
    """Rebuild (header, AgentRun) from a trace file."""
    header: dict | None = None
    run: AgentRun | None = None
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    for ev in events:
        kind = ev["type"]
        if kind == "run_start":
            header = ev["header"]
            run = AgentRun(
                id=header["run_id"],
                task_id=header["script"]["task"]["id"],
                agent=header["script"].get("agent", "scripted@0.1"),
                model=header["script"].get("model"),
                status=RunStatus.RUNNING,
                coverage=Coverage.model_validate(header["coverage"])
                if header.get("coverage")
                else None,
            )
        elif run is None:
            raise ValueError(f"trace {path} has events before run_start")
        elif kind == "step":
            run.steps.append(StepRecord.model_validate(ev["step"]))
        elif kind == "policy_decision":
            run.policy_decisions.append(PolicyDecision.model_validate(ev["decision"]))
        elif kind == "risk_signal":
            run.risk_signals.append(RiskSignal.model_validate(ev["signal"]))
        elif kind == "evidence":
            run.evidence.append(EvidenceItem.model_validate(ev["item"]))
        elif kind == "artifact":
            run.artifacts.append(Artifact.model_validate(ev["artifact"]))
        elif kind == "run_end":
            run.status = RunStatus(ev["status"])
            if ev.get("started_at"):
                run.started_at = datetime.fromisoformat(ev["started_at"])
            if ev.get("finished_at"):
                run.finished_at = datetime.fromisoformat(ev["finished_at"])
    if header is None or run is None:
        raise ValueError(f"trace {path} has no run_start event")
    return header, run
