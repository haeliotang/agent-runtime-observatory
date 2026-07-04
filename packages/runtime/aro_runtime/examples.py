"""Example discovery and execution.

An example directory is the unit of demo, eval, and regression:

    examples/<name>/
        script.json    # what the agent will do
        policy.yaml    # what it is allowed to do
        workspace/     # the files it does it to (copied into memory per run)
        expected.json  # what a correct run looks like (used by aro_evals)
        golden/trace.jsonl  # recorded reference trace (used for replay regression)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aro_schema import AgentRun

from aro_runtime.executor import execute_script
from aro_runtime.hooks import RunHooks
from aro_runtime.policy import Policy, PolicyEngine
from aro_runtime.script import Script
from aro_runtime.tools import Workspace


@dataclass
class Example:
    name: str
    dir: Path
    script: Script
    policy: Policy
    workspace_dir: Path
    expected: dict | None
    golden_trace: Path | None


def load_example(directory: Path) -> Example:
    directory = Path(directory)
    expected_path = directory / "expected.json"
    golden = directory / "golden" / "trace.jsonl"
    return Example(
        name=directory.name,
        dir=directory,
        script=Script.from_file(directory / "script.json"),
        policy=Policy.from_file(directory / "policy.yaml"),
        workspace_dir=directory / "workspace",
        expected=json.loads(expected_path.read_text()) if expected_path.exists() else None,
        golden_trace=golden if golden.exists() else None,
    )


def discover_examples(root: Path) -> dict[str, Example]:
    examples = {}
    for directory in sorted(Path(root).iterdir()):
        if directory.is_dir() and (directory / "script.json").exists():
            examples[directory.name] = load_example(directory)
    return examples


def run_example(
    example: Example,
    *,
    run_id: str | None = None,
    hooks: RunHooks | None = None,
    trace_path: Path | None = None,
) -> AgentRun:
    return execute_script(
        example.script,
        policy_engine=PolicyEngine(example.policy),
        workspace=Workspace.from_dir(example.workspace_dir),
        run_id=run_id,
        hooks=hooks,
        trace_path=trace_path,
    )
