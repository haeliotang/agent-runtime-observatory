"""Golden-task evaluation: run every example fresh, check it against its
expected.json, then replay its committed golden trace and require zero
divergence.

This is the regression gate CI runs on every change: touch the runtime, the
policy engine, or a tool, and any behavior drift shows up here as a diff
against recorded reality — not as a reviewer's opinion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aro_runtime import Example, Workspace, discover_examples, replay_trace, run_example


@dataclass
class EvalResult:
    example: str
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate_example(example: Example) -> EvalResult:
    result = EvalResult(example=example.name)
    expected = example.expected
    if expected is None:
        result.failures.append("no expected.json")
        return result

    run = run_example(example)

    if run.status.value != expected["final_status"]:
        result.failures.append(
            f"final_status: expected {expected['final_status']}, got {run.status.value}"
        )

    # Non-allow policy decisions must match expected.json exactly (no unexpected
    # denials, no missing ones). Recorded allow-decisions are informational.
    got = sorted(
        (d.step_index, d.rule_id, d.decision.value)
        for d in run.policy_decisions
        if d.decision.value != "allow"
    )
    want = sorted(
        (d["step_index"], d["rule_id"], d["decision"]) for d in expected.get("policy_decisions", [])
    )
    if got != want:
        result.failures.append(f"policy_decisions: expected {want}, got {got}")

    got_signals = sorted((s.step_index, s.severity.value, s.category) for s in run.risk_signals)
    want_signals = sorted(
        (s["step_index"], s["severity"], s["category"]) for s in expected.get("risk_signals", [])
    )
    if got_signals != want_signals:
        result.failures.append(f"risk_signals: expected {want_signals}, got {got_signals}")

    artifact_paths = {a.path for a in run.artifacts}
    for artifact in expected.get("artifacts", []):
        if artifact["path"] not in artifact_paths:
            result.failures.append(f"missing artifact: {artifact['path']}")

    if example.golden_trace is not None:
        report = replay_trace(example.golden_trace, Workspace.from_dir(example.workspace_dir))
        for div in report.divergences:
            result.failures.append(
                f"golden replay divergence at step {div.step_index} ({div.field}): "
                f"recorded={div.recorded!r} replayed={div.replayed!r}"
            )
    else:
        result.failures.append("no golden trace recorded (run scripts/record_goldens.py)")

    return result


def evaluate_all(examples_root: Path) -> list[EvalResult]:
    examples = discover_examples(examples_root)
    return [evaluate_example(example) for example in examples.values()]
