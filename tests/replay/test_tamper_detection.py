"""Replay must catch tampering: edited outputs and altered workspaces both
show up as divergences, not as silent passes."""

import json

from aro_runtime import Workspace, replay_trace


def _load_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tampered_output_digest_detected(examples_dir, tmp_path):
    golden = examples_dir / "coding-agent-run" / "golden" / "trace.jsonl"
    events = _load_lines(golden)
    for event in events:
        if event["type"] == "step" and event["step"]["output_digest"]:
            event["step"]["output_digest"] = "sha256:" + "0" * 64
            break
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    workspace = Workspace.from_dir(examples_dir / "coding-agent-run" / "workspace")
    report = replay_trace(tampered, workspace)
    assert not report.ok
    assert any(d.field == "output_digest" for d in report.divergences)


def test_modified_workspace_detected(examples_dir):
    golden = examples_dir / "coding-agent-run" / "golden" / "trace.jsonl"
    workspace = Workspace.from_dir(examples_dir / "coding-agent-run" / "workspace")
    workspace.files["app.py"] += "\n# drift\n"
    report = replay_trace(golden, workspace)
    assert not report.ok
    assert any(d.field == "workspace_digest" for d in report.divergences)
