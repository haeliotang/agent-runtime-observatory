from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_b1_live import (
    B1LeakRefs,
    SWEAgentB1LiveSingleSpec,
    run_sweagent_b1_live_single,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.b1_issue_repro import (
    attach_captured_traceback,
    b1_payload_leak_scan,
    build_b1_payload,
    capture_issue_repro,
    construct_repro_script,
    issue_repro_eligibility,
)
from wutai_clinic.intervention.protocol_b1 import protocol_b1_template
from wutai_clinic.intervention.protocol_b1_hook import (
    ProtocolB1InjectionHook,
)

runner = CliRunner()

ISSUE_WITH_REPRO = """Bug: foo() crashes on empty input.

To reproduce:
```
>>> from pkg import foo
>>> foo("")
Traceback (most recent call last):
ValueError: empty
```
"""


# --- scientific core: eligibility ----------------------------------------------
def test_eligibility_true_when_issue_has_actionable_repro() -> None:
    e = issue_repro_eligibility("pkg__pkg-1", ISSUE_WITH_REPRO)
    assert e.eligible is True
    assert "code_or_command_block" in e.markers


def test_eligibility_false_for_prose_only_and_empty() -> None:
    assert issue_repro_eligibility("a", "Please make it faster, it feels slow.").eligible is False
    assert issue_repro_eligibility("b", "").eligible is False


# --- scientific core: payload + M2b leak scan ----------------------------------
def test_build_payload_is_issue_text_only() -> None:
    p = build_b1_payload(instance_id="x", problem_statement=ISSUE_WITH_REPRO, repro_traceback="ValueError: empty")
    assert p["payload_provenance"] == "issue_text_only"
    assert "foo" in p["issue_reproduction_steps"]


def test_leak_scan_clean_payload() -> None:
    p = build_b1_payload(instance_id="x", problem_statement=ISSUE_WITH_REPRO, repro_traceback="ValueError: empty")
    assert b1_payload_leak_scan(p, fail_to_pass=["tests/test_foo.py::test_empty"]) == []


def test_leak_scan_catches_fail_to_pass_node_id() -> None:
    p = build_b1_payload(instance_id="x", problem_statement="see tests/test_foo.py::test_empty", repro_traceback=None)
    findings = b1_payload_leak_scan(p, fail_to_pass=["tests/test_foo.py::test_empty"])
    assert any("fail_to_pass_node_in_payload" in f for f in findings)


def test_leak_scan_catches_official_test_token_and_bad_provenance() -> None:
    leak = {
        "payload_provenance": "official_test_derived",
        "issue_reproduction_steps": "run FAIL_TO_PASS",
        "issue_derived_repro_traceback": None,
    }
    findings = b1_payload_leak_scan(leak)
    assert any(f.startswith("provenance_not_issue_text_only") for f in findings)
    assert any("token:fail_to_pass" in f for f in findings)


def test_leak_scan_ignores_diff_context_but_catches_added_fix_line() -> None:
    # A diff CONTEXT line shared with the issue repro is NOT a leak (false positive
    # that blocked every treatment arm); only ADDED ('+') fix lines are.
    gold_diff = (
        "diff --git a/m.py b/m.py\n@@ -1,3 +1,3 @@\n"
        " if ra_dec_order and sky == 'input':\n"
        "-    old_buggy_line_that_is_long_enough_xx\n"
        "+    fixed_line_the_actual_solution_here\n"
    )
    ctx = {"payload_provenance": "issue_text_only", "issue_reproduction_steps": "if ra_dec_order and sky == 'input':", "issue_derived_repro_traceback": None}
    assert b1_payload_leak_scan(ctx, gold_patch=gold_diff) == []  # context overlap is fine
    leak = {"payload_provenance": "issue_text_only", "issue_reproduction_steps": "fixed_line_the_actual_solution_here", "issue_derived_repro_traceback": None}
    assert "gold_patch_line_in_payload" in b1_payload_leak_scan(leak, gold_patch=gold_diff)


def test_leak_scan_catches_test_patch_and_gold_lines() -> None:
    test_patch = "def test_empty():\n    assert foo('') is None  # the hidden official assertion line"
    p = {
        "payload_provenance": "issue_text_only",
        "issue_reproduction_steps": "assert foo('') is None  # the hidden official assertion line",
        "issue_derived_repro_traceback": None,
    }
    assert "test_patch_line_in_payload" in b1_payload_leak_scan(p, test_patch=test_patch)


# --- injection hook (captures in live container at first model query, injects once) ---
def test_hook_captures_and_injects_exactly_once() -> None:
    hook = ProtocolB1InjectionHook(
        protocol=protocol_b1_template(),
        issue_reproduction_steps=ISSUE_WITH_REPRO,
        source_task_id="x",
        capture_executor=lambda script: "Traceback (most recent call last):\nValueError: empty",
    )
    hook.on_model_query(messages=[], agent="main")
    hook.on_model_query(messages=[], agent="main")  # second query must NOT re-capture/re-inject
    assert hook.injection_count == 1
    assert hook.captured_traceback.startswith("Traceback")
    assert hook.payload["issue_derived_repro_traceback"].startswith("Traceback")
    assert sum(1 for e in hook.audit_events if e["event"] == "protocol_b1_injection") == 1


def test_hook_voids_on_captured_traceback_leak() -> None:
    # the live repro output surfaces a FAIL_TO_PASS node id -> M2b voids, no injection
    hook = ProtocolB1InjectionHook(
        protocol=protocol_b1_template(),
        issue_reproduction_steps=ISSUE_WITH_REPRO,
        source_task_id="x",
        capture_executor=lambda script: "Traceback...\n at tests/test_foo.py::test_empty",
        fail_to_pass=["tests/test_foo.py::test_empty"],
    )
    hook.on_model_query(messages=[], agent="main")
    assert hook.injection_count == 0
    assert any("fail_to_pass_node_in_payload" in f for f in hook.capture_leak_findings)


def test_hook_capture_error_degrades_to_steps_only() -> None:
    # an executor failure must NOT kill the run — inject steps with traceback=None
    def boom(_script):
        raise RuntimeError("container exec failed")

    hook = ProtocolB1InjectionHook(
        protocol=protocol_b1_template(),
        issue_reproduction_steps=ISSUE_WITH_REPRO,
        source_task_id="x",
        capture_executor=boom,
    )
    hook.on_model_query(messages=[], agent="main")
    assert hook.injection_count == 1
    assert hook.captured_traceback is None


# --- adapter (offline, no execute) ---------------------------------------------
def _spec(tmp_path: Path, arm: str, payload=None, leak=B1LeakRefs()):
    cfg = tmp_path / "run_single.json"
    cfg.write_text("{}")
    return SWEAgentB1LiveSingleSpec(
        config_path=cfg,
        output_dir=tmp_path / arm,
        protocol=protocol_b1_template(),
        arm_type=arm,  # type: ignore[arg-type]
        payload=payload,
        leak_refs=leak,
        execute=False,
        source_task_id="pkg__pkg-1",
    )


def test_adapter_treatment_plan_clean(tmp_path: Path) -> None:
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback="t")
    res = run_sweagent_b1_live_single(spec=_spec(tmp_path, "treatment", payload), policy=RuntimePermissionPolicy())
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_planned_no_run"
    assert r["passed"] is True
    assert r["gates"]["m2b_payload_leak_scan_clean"] is True
    assert r["gates"]["no_unrequested_run"] is True


def test_adapter_treatment_leak_is_blocked_even_in_plan(tmp_path: Path) -> None:
    payload = {
        "payload_provenance": "issue_text_only",
        "issue_reproduction_steps": "run tests/test_foo.py::test_empty",
        "issue_derived_repro_traceback": None,
    }
    leak = B1LeakRefs(fail_to_pass=["tests/test_foo.py::test_empty"])
    res = run_sweagent_b1_live_single(spec=_spec(tmp_path, "treatment", payload, leak), policy=RuntimePermissionPolicy())
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_blocked_payload_leak"
    assert r["passed"] is False
    assert r["m2b_leak_findings"]


def test_adapter_control_plan_needs_no_payload(tmp_path: Path) -> None:
    res = run_sweagent_b1_live_single(spec=_spec(tmp_path, "control"), policy=RuntimePermissionPolicy())
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_planned_no_run"
    assert r["passed"] is True


# --- issue-derived reproduction capture (step ②) ------------------------------
def test_construct_repro_script_extracts_repl_input_drops_output() -> None:
    script = construct_repro_script(ISSUE_WITH_REPRO)
    assert "from pkg import foo" in script
    assert 'foo("")' in script
    # transcript OUTPUT lines must be dropped (not run as code)
    assert "Traceback (most recent call last)" not in script
    assert "ValueError: empty" not in script


def test_construct_repro_script_empty_when_no_code() -> None:
    assert construct_repro_script("just prose, no code or commands") == ""


def test_capture_issue_repro_with_fake_executor() -> None:
    captured = "Traceback (most recent call last):\n  File x\nValueError: empty"
    res = capture_issue_repro(
        issue_reproduction_steps=ISSUE_WITH_REPRO,
        executor=lambda script: "noise before\n" + captured,
    )
    assert res.ran is True
    assert res.traceback.startswith("Traceback (most recent call last)")
    assert "noise before" not in res.traceback  # only the traceback slice is kept


def test_capture_then_m2b_rescan_catches_official_test_in_traceback() -> None:
    # The issue steps are clean, but the live repro output happens to surface a
    # FAIL_TO_PASS node id -> the post-capture re-scan must catch it.
    payload = build_b1_payload(instance_id="x", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    leaky_tb = "Traceback ...\n  at tests/test_foo.py::test_empty"
    payload = attach_captured_traceback(payload, leaky_tb)
    findings = b1_payload_leak_scan(payload, fail_to_pass=["tests/test_foo.py::test_empty"])
    assert any("fail_to_pass_node_in_payload" in f for f in findings)


# --- adapter execute path (fakes: exercises the capture->rescan->inject wiring) -
class _FakeModel:
    def query(self, *a, **k):
        return {}


class _FakeAgent:
    def __init__(self):
        self.model = _FakeModel()
        self.hooks = []

    def add_hook(self, hook):
        self.hooks.append(hook)


class _FakeResult:
    def __init__(self, exit_status, submission="diff --git a/x b/x\n+fix"):
        self.info = {"exit_status": exit_status, "submission": submission}


class _FakeRunSingle:
    def __init__(self, exit_status="submitted", submission="diff --git a/x b/x\n+fix"):
        self.agent = _FakeAgent()
        self.env = None
        self._exit_status = exit_status
        self._submission = submission

    def run(self):
        for h in self.agent.hooks:
            h.on_model_query(messages=[], agent="main")
        return _FakeResult(self._exit_status, self._submission)


class _FakeProblem:
    def __init__(self, pid):
        self.id = pid


class _NativeRunSingle:
    """Mimics real SWE-agent RunSingle: run() returns None and the patch/exit_status
    are saved to output_dir/<pid>/ (the path _read_native_outcome reads)."""

    def __init__(self, output_dir, pid="pkg__pkg-1", exit_status="submitted", patch="diff --git a/n b/n\n+native"):
        self.agent = _FakeAgent()
        self.output_dir = Path(output_dir)
        self.problem_statement = _FakeProblem(pid)
        self._exit = exit_status
        self._patch = patch

    def run(self):
        for h in self.agent.hooks:
            h.on_model_query(messages=[], agent="main")
        d = self.output_dir / self.problem_statement.id
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.problem_statement.id}.pred").write_text(
            json.dumps({"instance_id": self.problem_statement.id, "model_name_or_path": "x", "model_patch": self._patch})
        )
        (d / f"{self.problem_statement.id}.traj").write_text(json.dumps({"info": {"exit_status": self._exit}}))
        return None  # RunSingle.run() returns None — patch is on disk


def test_adapter_reads_patch_from_native_output_when_run_returns_none(tmp_path) -> None:
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    rs = _NativeRunSingle(tmp_path / "native", exit_status="submitted", patch="diff --git a/n b/n\n+native")
    res = run_sweagent_b1_live_single(
        spec=_exec_spec(tmp_path, payload, executor=lambda s: "tb"),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda p: rs,
    )
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_run_completed"
    assert r["run_exit_status"] == "submitted"
    assert r["patch_non_empty"] is True
    assert (tmp_path / "arm" / "b1_live_arm.patch").read_text() == "diff --git a/n b/n\n+native"


def test_adapter_native_exit_error_flagged(tmp_path) -> None:
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    rs = _NativeRunSingle(tmp_path / "native", exit_status="exit_error", patch="")
    res = run_sweagent_b1_live_single(
        spec=_exec_spec(tmp_path, payload, executor=lambda s: "tb"),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda p: rs,
    )
    assert res["report"]["decision"] == "route_b1_live_arm_run_failed"
    assert res["report"]["run_exit_ok"] is False


def _exec_spec(tmp_path, payload, leak=B1LeakRefs(), executor=None):
    cfg = tmp_path / "run_single.json"
    cfg.write_text("{}")
    return SWEAgentB1LiveSingleSpec(
        config_path=cfg,
        output_dir=tmp_path / "arm",
        protocol=protocol_b1_template(),
        arm_type="treatment",
        payload=payload,
        leak_refs=leak,
        execute=True,
        source_task_id="pkg__pkg-1",
        repro_executor=executor,
    )


def test_adapter_execute_captures_and_injects_once(tmp_path) -> None:
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    spec = _exec_spec(tmp_path, payload, executor=lambda s: "Traceback (most recent call last):\nValueError: empty")
    res = run_sweagent_b1_live_single(
        spec=spec,
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda p: _FakeRunSingle(),
    )
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_run_completed"
    assert r["issue_repro_traceback_captured"] is True
    assert r["injection_count"] == 1
    # persisted payload carries the live-captured traceback
    persisted = json.loads((tmp_path / "arm" / "b1_live_arm_payload.json").read_text())
    assert "ValueError: empty" in persisted["issue_derived_repro_traceback"]
    # the agent patch is archived per-arm so official eval can read THIS run
    assert (tmp_path / "arm" / "b1_live_arm.patch").read_text().startswith("diff --git")
    assert r["patch_non_empty"] is True


def test_adapter_execute_flags_exit_error_as_run_failed(tmp_path) -> None:
    # SWE-agent exit_status=exit_error (e.g. provider 'Insufficient Balance') must
    # NOT be reported as run_completed — it would contaminate the verdict.
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    res = run_sweagent_b1_live_single(
        spec=_exec_spec(tmp_path, payload, executor=lambda s: "Traceback...\nValueError: x"),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda p: _FakeRunSingle(exit_status="exit_error"),
    )
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_run_failed"
    assert r["run_exit_ok"] is False
    assert r["run_exit_status"] == "exit_error"


def test_adapter_execute_voids_on_captured_leak(tmp_path) -> None:
    # The capture happens DURING the run; if the live repro output leaks a
    # FAIL_TO_PASS node id, the hook records it + skips injection. The run itself
    # completes, but the arm is voided (route-b1-cells excludes it via leak_clean).
    payload = build_b1_payload(instance_id="pkg__pkg-1", problem_statement=ISSUE_WITH_REPRO, repro_traceback=None)
    leak = B1LeakRefs(fail_to_pass=["tests/test_foo.py::test_empty"])
    spec = _exec_spec(tmp_path, payload, leak=leak, executor=lambda s: "Traceback...\n tests/test_foo.py::test_empty")
    res = run_sweagent_b1_live_single(
        spec=spec,
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda p: _FakeRunSingle(),
    )
    r = res["report"]
    assert r["decision"] == "route_b1_live_arm_capture_leak_void"
    assert r["injection_count"] == 0
    assert r["m2b_capture_leak_findings"]


# --- CLI ----------------------------------------------------------------------
def test_cli_eligibility_screen(tmp_path: Path) -> None:
    ps = tmp_path / "ps.jsonl"
    ps.write_text(
        json.dumps({"instance_id": "pkg__pkg-1", "problem_statement": ISSUE_WITH_REPRO})
        + "\n"
        + json.dumps({"instance_id": "pkg__pkg-2", "problem_statement": "just make it nicer"})
        + "\n"
    )
    res = runner.invoke(app, ["route-b1-eligibility-screen", str(ps), "-o", str(tmp_path / "elig")])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["eligible"] == ["pkg__pkg-1"]


def test_cli_live_arm_plan(tmp_path: Path) -> None:
    cfg = tmp_path / "run_single.json"
    cfg.write_text("{}")
    issue = tmp_path / "issue.txt"
    issue.write_text(ISSUE_WITH_REPRO)
    res = runner.invoke(
        app,
        [
            "route-b1-live-arm", str(cfg), "-o", str(tmp_path / "arm"),
            "--arm", "treatment", "--source-task-id", "pkg__pkg-1",
            "--problem-statement-file", str(issue),
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["decision"] == "route_b1_live_arm_planned_no_run"
    assert out["m2b_leak_findings"] == []
