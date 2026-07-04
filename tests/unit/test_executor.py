from aro_runtime import PolicyEngine, Script, Workspace, execute_script, load_trace
from aro_runtime.policy import Policy
from aro_schema import RunStatus

BASE_SCRIPT = {
    "task": {"id": "t1", "title": "test", "goal_id": "g1"},
    "goal": {"id": "g1", "statement": "test goal", "owner_seat_id": "seat-1"},
    "steps": [],
}

DENY_SHELL_POLICY = Policy.model_validate(
    {
        "id": "p1",
        "default": "allow",
        "rules": [
            {
                "id": "deny-shell",
                "tool": "shell",
                "args_regex": "rm -rf",
                "action": "deny",
                "severity": "high",
                "category": "destructive-shell",
            }
        ],
    }
)


def make_script(steps):
    return Script.model_validate({**BASE_SCRIPT, "steps": steps})


def test_step_output_reference_interpolation():
    script = make_script(
        [
            {"tool": "read_file", "args": {"path": "a.txt"}},
            {"tool": "write_file", "args": {"path": "b.txt", "content": "got: ${step:0.output}"}},
        ]
    )
    ws = Workspace({"a.txt": "hello"})
    run = execute_script(script, policy_engine=PolicyEngine(DENY_SHELL_POLICY), workspace=ws)
    assert run.status == RunStatus.COMPLETED
    assert ws.files["b.txt"] == "got: hello"


def test_denied_step_does_not_fail_run():
    script = make_script(
        [
            {"tool": "shell", "args": {"cmd": "rm -rf /"}},
            {"tool": "write_file", "args": {"path": "out.txt", "content": "still ran"}},
        ]
    )
    run = execute_script(
        script, policy_engine=PolicyEngine(DENY_SHELL_POLICY), workspace=Workspace()
    )
    assert run.status == RunStatus.COMPLETED
    assert run.steps[0].error == "blocked by policy rule deny-shell"
    assert run.steps[0].output_digest is None
    assert len(run.policy_decisions) == 1
    assert len(run.risk_signals) == 1
    assert run.steps[1].output_digest is not None


def test_tool_error_fails_run():
    script = make_script([{"tool": "read_file", "args": {"path": "missing.txt"}}])
    run = execute_script(
        script, policy_engine=PolicyEngine(DENY_SHELL_POLICY), workspace=Workspace()
    )
    assert run.status == RunStatus.FAILED
    assert "file not found" in run.steps[0].error


def test_trace_roundtrip(tmp_path):
    script = make_script(
        [
            {"tool": "read_file", "args": {"path": "a.txt"}},
            {"tool": "shell", "args": {"cmd": "rm -rf /"}},
        ]
    )
    trace_path = tmp_path / "trace.jsonl"
    run = execute_script(
        script,
        policy_engine=PolicyEngine(DENY_SHELL_POLICY),
        workspace=Workspace({"a.txt": "hi"}),
        trace_path=trace_path,
    )
    header, restored = load_trace(trace_path)
    assert header["run_id"] == run.id
    assert restored.status == run.status
    assert [s.input_digest for s in restored.steps] == [s.input_digest for s in run.steps]
    assert [d.rule_id for d in restored.policy_decisions] == ["deny-shell"]
