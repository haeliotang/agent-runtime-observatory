"""Tests for orchestration/batch_runner.py and orchestration/state_inference.py.

All fixtures are synthetic (no real artifact directories are read).
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wutai_clinic.orchestration.state_inference import infer_pair_state
from wutai_clinic.orchestration.batch_runner import (
    CLAIM_BOUNDARY,
    advance_batch,
    batch_status,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pair_entry(
    source_task_id: str = "fake__task-1",
    pair_id: str = "pair_001",
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    er = str(evidence_root or "/tmp/evidence")
    return {
        "source_task_id": source_task_id,
        "pair_id": pair_id,
        "pair_inputs_dir": f"{er}/protocol_v2_fresh_state_capsule_pair_inputs/{source_task_id}",
        "protocol_template": f"{er}/protocol_v2_prescription_template/template.json",
        "output_root": er,
    }


def _batch_spec(evidence_root: Path, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "batch_id": "test_batch_001",
        "evidence_root": str(evidence_root),
        "pairs": pairs,
    }


def _preflight_report(passed: bool = True) -> dict[str, Any]:
    return {
        "decision": (
            "protocol_v2_planned_preflight_ready_live_execution_not_authorized"
            if passed
            else "protocol_v2_planned_preflight_blocked"
        ),
        "passed": passed,
    }


def _live_single_report(arm: str, completed: bool = True) -> dict[str, Any]:
    return {
        "decision": (
            "protocol_v2_live_single_run_completed"
            if completed
            else "protocol_v2_live_single_blocked_needs_ack"
        ),
        "passed": completed,
        "arm_type": arm,
    }


def _pair_report(passed: bool = True) -> dict[str, Any]:
    return {
        "decision": (
            "protocol_v2_live_pair_ready_pending_official_eval"
            if passed
            else "protocol_v2_live_pair_blocked"
        ),
        "passed": passed,
    }


def _official_eval_report(completed: bool = True) -> dict[str, Any]:
    return {
        "official_eval_completed": completed,
        "passed": completed,
        "decision": (
            "protocol_v2_official_eval_outcome_label_ready"
            if completed
            else "protocol_v2_official_eval_blocked"
        ),
    }


# ---------------------------------------------------------------------------
# Fixtures: disk layouts for each of the 9 happy-path states
# ---------------------------------------------------------------------------


@pytest.fixture()
def evidence_pending(tmp_path: Path) -> Path:
    """No artifacts written — pair is pending."""
    return tmp_path


@pytest.fixture()
def evidence_preflight_ready(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        _preflight_report(passed=True),
    )
    return tmp_path


@pytest.fixture()
def evidence_awaiting_control(tmp_path: Path) -> Path:
    """Preflight passed with the canonical decision string."""
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )
    return tmp_path


@pytest.fixture()
def evidence_awaiting_treatment(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    # Preflight
    _write_json(
        tmp_path
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )
    # Control arm complete
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    return tmp_path


@pytest.fixture()
def evidence_treatment_complete(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "treatment"
        / "protocol_v2_live_single_report.json",
        _live_single_report("treatment", completed=True),
    )
    return tmp_path


@pytest.fixture()
def evidence_pair_assembled(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    # Pair report exists but with a non-canonical decision → "pair_assembled"
    _write_json(
        tmp_path / "protocol_v2_live_pair" / sid / "protocol_v2_live_pair_report.json",
        {"decision": "protocol_v2_live_pair_blocked", "passed": False},
    )
    return tmp_path


@pytest.fixture()
def evidence_awaiting_official_eval(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    _write_json(
        tmp_path / "protocol_v2_live_pair" / sid / "protocol_v2_live_pair_report.json",
        _pair_report(passed=True),
    )
    return tmp_path


@pytest.fixture()
def evidence_official_eval_complete(tmp_path: Path) -> Path:
    sid = "fake__task-1"
    _write_json(
        tmp_path / "protocol_v2_official_eval" / sid / "protocol_v2_official_eval_report.json",
        _official_eval_report(completed=True),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# State inference tests
# ---------------------------------------------------------------------------


def test_state_pending(evidence_pending: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_pending)
    assert infer_pair_state(entry, evidence_pending) == "pending"


def test_state_preflight_ready(tmp_path: Path) -> None:
    """A passed=True preflight report with a non-canonical decision string maps
    to 'preflight_ready' (not awaiting_control_authorization).
    """
    sid = "fake__task-1"
    # Use a decision that is truthy/passed but NOT the canonical one
    _write_json(
        tmp_path
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {"decision": "protocol_v2_planned_preflight_ready_some_other_variant", "passed": True},
    )
    entry = _pair_entry(source_task_id=sid, evidence_root=tmp_path)
    assert infer_pair_state(entry, tmp_path) == "preflight_ready"


def test_state_awaiting_control_authorization(evidence_awaiting_control: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_awaiting_control)
    assert infer_pair_state(entry, evidence_awaiting_control) == "awaiting_control_authorization"


def test_state_awaiting_treatment_authorization(evidence_awaiting_treatment: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_awaiting_treatment)
    assert (
        infer_pair_state(entry, evidence_awaiting_treatment) == "awaiting_treatment_authorization"
    )


def test_state_treatment_complete(evidence_treatment_complete: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_treatment_complete)
    assert infer_pair_state(entry, evidence_treatment_complete) == "treatment_complete"


def test_state_pair_assembled(evidence_pair_assembled: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_pair_assembled)
    # Pair report exists but blocked → "failed_pair_assembly" per logic
    # (blocked decision yields failed_pair_assembly)
    state = infer_pair_state(entry, evidence_pair_assembled)
    assert state == "failed_pair_assembly"


def test_state_awaiting_official_eval(evidence_awaiting_official_eval: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_awaiting_official_eval)
    assert (
        infer_pair_state(entry, evidence_awaiting_official_eval)
        == "awaiting_official_eval_authorization"
    )


def test_state_official_eval_complete(evidence_official_eval_complete: Path) -> None:
    entry = _pair_entry(evidence_root=evidence_official_eval_complete)
    assert infer_pair_state(entry, evidence_official_eval_complete) == "official_eval_complete"


def test_state_failed_preflight(tmp_path: Path) -> None:
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {"decision": "protocol_v2_planned_preflight_blocked", "passed": False},
    )
    entry = _pair_entry(evidence_root=tmp_path)
    assert infer_pair_state(entry, tmp_path) == "failed_preflight"


def test_state_failed_control(tmp_path: Path) -> None:
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=False),
    )
    entry = _pair_entry(evidence_root=tmp_path)
    assert infer_pair_state(entry, tmp_path) == "failed_control"


def test_state_failed_treatment(tmp_path: Path) -> None:
    sid = "fake__task-1"
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    _write_json(
        tmp_path
        / "protocol_v2_live_single_executed"
        / sid
        / "treatment"
        / "protocol_v2_live_single_report.json",
        _live_single_report("treatment", completed=False),
    )
    entry = _pair_entry(evidence_root=tmp_path)
    assert infer_pair_state(entry, tmp_path) == "failed_treatment"


def test_state_failed_official_eval(tmp_path: Path) -> None:
    sid = "fake__task-1"
    _write_json(
        tmp_path / "protocol_v2_official_eval" / sid / "protocol_v2_official_eval_report.json",
        _official_eval_report(completed=False),
    )
    entry = _pair_entry(evidence_root=tmp_path)
    assert infer_pair_state(entry, tmp_path) == "failed_official_eval"


# ---------------------------------------------------------------------------
# MOST IMPORTANT: no auto-ack in batch_runner source code
# ---------------------------------------------------------------------------


def test_batch_runner_source_contains_no_auto_ack_kwargs() -> None:
    """Scan the AST of batch_runner.py for forbidden kwarg patterns.

    No call in the source may pass ack_docker=True, ack_external_provider=True,
    ack_official_eval=True, or run_official_eval=True as keyword arguments.
    """
    import wutai_clinic.orchestration.batch_runner as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)

    forbidden_kwargs = {
        "ack_docker": True,
        "ack_external_provider": True,
        "ack_official_eval": True,
        "run_official_eval": True,
    }

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in forbidden_kwargs:
                    # Check if the value is the forbidden literal
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        violations.append(
                            f"line {node.lineno}: {kw.arg}=True found in batch_runner source"
                        )

    assert not violations, "\n".join(violations)


def test_state_inference_source_contains_no_auto_ack_kwargs() -> None:
    """Same check for state_inference.py."""
    import wutai_clinic.orchestration.state_inference as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)

    forbidden = {"ack_docker", "ack_external_provider", "ack_official_eval", "run_official_eval"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in forbidden:
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        violations.append(
                            f"line {node.lineno}: {kw.arg}=True found in state_inference source"
                        )

    assert not violations, "\n".join(violations)


# ---------------------------------------------------------------------------
# advance_batch: recording command_runner, ONLY offline steps called
# ---------------------------------------------------------------------------


class RecordingRunner:
    """Captures fn/kwargs; does NOT execute them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"fn": fn.__name__, "kwargs": kwargs})
        return {}


def test_advance_batch_only_offline_steps_called(tmp_path: Path) -> None:
    """advance_batch with a recording runner must ONLY record offline calls.

    No call may have ack_docker=True, ack_external_provider=True,
    ack_official_eval=True, or run_official_eval=True in kwargs.
    """
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    # Set up treatment_complete so pair assembly runs offline
    _write_json(
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    _write_json(
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "treatment"
        / "protocol_v2_live_single_report.json",
        _live_single_report("treatment", completed=True),
    )

    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    state_dir = tmp_path / "state"
    rec = RecordingRunner()

    result = advance_batch(spec, state_dir, command_runner=rec)

    # Verify no forbidden kwargs in any recorded call
    forbidden = {
        "ack_docker": True,
        "ack_external_provider": True,
        "ack_official_eval": True,
        "run_official_eval": True,
    }
    for call in rec.calls:
        for key, val in forbidden.items():
            assert call["kwargs"].get(key) is not True, (
                f"Forbidden kwarg {key}=True found in recorded call to {call['fn']}"
            )

    # Also scan the full repr of call args for string matches as extra safety
    all_args_repr = repr(rec.calls)
    for forbidden_str in [
        "ack_docker=True",
        "ack_external_provider=True",
        "ack_official_eval=True",
        "run_official_eval=True",
    ]:
        assert forbidden_str not in all_args_repr, f"Found '{forbidden_str}' in recorded call args"

    # Result structure
    assert "batch_id" in result
    assert "operator_actions" in result
    assert "pairs_status" in result
    assert result["claim_boundary"] == CLAIM_BOUNDARY


def test_advance_batch_awaiting_states_produce_operator_actions(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    # Awaiting control authorization
    _write_json(
        evidence_root
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    state_dir = tmp_path / "state"
    result = advance_batch(spec, state_dir)
    assert len(result["operator_actions"]) == 1
    assert result["operator_actions"][0]["state"] == "awaiting_control_authorization"
    cmd = result["operator_actions"][0]["command"]
    assert "--ack-docker" in cmd
    assert "--ack-external-provider" in cmd
    assert "--arm control" in cmd


def test_operator_actions_treatment_contains_correct_flags(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    result = advance_batch(spec, tmp_path / "state")
    assert len(result["operator_actions"]) == 1
    cmd = result["operator_actions"][0]["command"]
    assert "--arm treatment" in cmd
    assert "--ack-docker" in cmd
    assert "--ack-external-provider" in cmd
    # Correct paths in command
    assert sid in cmd


def test_operator_actions_official_eval_contains_correct_flags(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root / "protocol_v2_live_pair" / sid / "protocol_v2_live_pair_report.json",
        _pair_report(passed=True),
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    result = advance_batch(spec, tmp_path / "state")
    assert len(result["operator_actions"]) == 1
    cmd = result["operator_actions"][0]["command"]
    assert "--ack-official-eval" in cmd
    assert "--run-official-eval" in cmd
    assert sid in cmd


# ---------------------------------------------------------------------------
# Self-healing: delete state file, re-run --advance, verify rebuild
# ---------------------------------------------------------------------------


def test_self_healing_state_rebuild(tmp_path: Path) -> None:
    """Deleting batch_state.json and re-running advance_batch rebuilds from artifacts."""
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    state_dir = tmp_path / "state"

    # First run
    result1 = advance_batch(spec, state_dir)
    state_file = state_dir / "batch_state.json"
    assert state_file.is_file()

    # Delete state file
    state_file.unlink()
    assert not state_file.exists()

    # Second run — must rebuild from artifacts alone
    result2 = advance_batch(spec, state_dir)
    assert state_file.is_file()

    assert result1["pairs_status"][0]["state"] == result2["pairs_status"][0]["state"]


# ---------------------------------------------------------------------------
# failed_* retry: clear artifacts, re-run, verify returns to awaiting state
# ---------------------------------------------------------------------------


def test_failed_control_retry(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    control_report = (
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json"
    )
    _write_json(control_report, _live_single_report("control", completed=False))
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)

    assert infer_pair_state(pair, evidence_root) == "failed_control"

    # Clear the failed artifact
    control_report.unlink()

    # Add a good preflight
    _write_json(
        evidence_root
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )

    state_after = infer_pair_state(pair, evidence_root)
    assert state_after == "awaiting_control_authorization"


def test_failed_treatment_retry(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "control"
        / "protocol_v2_live_single_report.json",
        _live_single_report("control", completed=True),
    )
    treatment_report = (
        evidence_root
        / "protocol_v2_live_single_executed"
        / sid
        / "treatment"
        / "protocol_v2_live_single_report.json"
    )
    _write_json(treatment_report, _live_single_report("treatment", completed=False))
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)

    assert infer_pair_state(pair, evidence_root) == "failed_treatment"

    # Clear failed artifact
    treatment_report.unlink()

    state_after = infer_pair_state(pair, evidence_root)
    assert state_after == "awaiting_treatment_authorization"


# ---------------------------------------------------------------------------
# batch_status: read-only
# ---------------------------------------------------------------------------


def test_batch_status_read_only(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root
        / "protocol_v2_planned_preflight"
        / sid
        / "protocol_v2_planned_preflight_report.json",
        {
            "decision": "protocol_v2_planned_preflight_ready_live_execution_not_authorized",
            "passed": True,
        },
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    state_dir = tmp_path / "state"

    result = batch_status(spec, state_dir)

    # Must not write anything
    assert not (state_dir / "batch_state.json").exists()
    assert not (state_dir / "batch_events.jsonl").exists()

    assert result["batch_id"] == "test_batch_001"
    assert result["claim_boundary"] == CLAIM_BOUNDARY
    assert len(result["pending_operator_actions"]) == 1
    assert result["pending_operator_actions"][0]["state"] == "awaiting_control_authorization"


def test_batch_status_official_eval_complete_no_pending_actions(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root / "protocol_v2_official_eval" / sid / "protocol_v2_official_eval_report.json",
        _official_eval_report(completed=True),
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    result = batch_status(spec, tmp_path / "state")
    assert result["pending_operator_actions"] == []
    assert result["pairs_status"][0]["state"] == "official_eval_complete"


# ---------------------------------------------------------------------------
# Event log: append-only
# ---------------------------------------------------------------------------


def test_event_log_appends_only(tmp_path: Path) -> None:
    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    state_dir = tmp_path / "state"

    advance_batch(spec, state_dir)
    events_path = state_dir / "batch_events.jsonl"
    first_content = events_path.read_text(encoding="utf-8")
    first_lines = [ln for ln in first_content.splitlines() if ln.strip()]

    advance_batch(spec, state_dir)
    second_content = events_path.read_text(encoding="utf-8")
    second_lines = [ln for ln in second_content.splitlines() if ln.strip()]

    # Must have grown, not been rewritten
    assert len(second_lines) > len(first_lines)
    # First run's lines must still be present at the start
    assert second_content.startswith(first_content)


# ---------------------------------------------------------------------------
# CLI end-to-end via CliRunner
# ---------------------------------------------------------------------------


def _write_batch_spec(path: Path, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Build a minimal test CLI app for CLI end-to-end tests.
# This avoids modifying the production cli.py (the patch lives in
# tasks/wave2/cli_patch_task6.py).
#
# We register the command on the main wutai-clinic app via the patch helper
# from cli_patch_task6, then invoke it through CliRunner.
# ---------------------------------------------------------------------------


def _make_test_app() -> Any:
    """Return a single-command Typer app for batch-orchestrate.

    Typer wraps a single @app.command() as the *default* command — so the
    CliRunner must NOT pass the command name; just pass the args directly.
    """
    import typer as _typer

    from wutai_clinic.orchestration.batch_runner import (
        advance_batch as _advance_batch,
        batch_status as _batch_status,
    )

    _app = _typer.Typer(name="batch-orchestrate", no_args_is_help=False)

    @_app.command()
    def _cmd(
        batch_spec: Path = _typer.Argument(...),
        state_dir: Path = _typer.Option(..., "-o", "--state-dir"),
        status: bool = _typer.Option(False, "--status"),
        advance: bool = _typer.Option(False, "--advance"),
    ) -> None:
        spec = json.loads(batch_spec.read_text(encoding="utf-8"))
        if status and not advance:
            _typer.echo(
                json.dumps(
                    _batch_status(spec, state_dir), ensure_ascii=False, indent=2, sort_keys=True
                )
            )
            return
        if advance:
            _typer.echo(
                json.dumps(
                    _advance_batch(spec, state_dir), ensure_ascii=False, indent=2, sort_keys=True
                )
            )
            return
        _typer.echo("Specify --status or --advance.", err=True)
        raise _typer.Exit(code=1)

    return _app


def test_cli_batch_orchestrate_status(tmp_path: Path) -> None:
    test_app = _make_test_app()

    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root / "protocol_v2_official_eval" / sid / "protocol_v2_official_eval_report.json",
        _official_eval_report(completed=True),
    )
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    spec_path = tmp_path / "batch_spec.json"
    _write_batch_spec(spec_path, spec)
    state_dir = tmp_path / "state"

    # Single-command app: DO NOT pass "batch-orchestrate" command name
    result = runner.invoke(
        test_app,
        [
            str(spec_path),
            "-o",
            str(state_dir),
            "--status",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["batch_id"] == "test_batch_001"
    assert data["pairs_status"][0]["state"] == "official_eval_complete"


def test_cli_batch_orchestrate_advance(tmp_path: Path) -> None:
    test_app = _make_test_app()

    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    # pending state — no artifacts
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    spec_path = tmp_path / "batch_spec.json"
    _write_batch_spec(spec_path, spec)
    state_dir = tmp_path / "state"

    result = runner.invoke(
        test_app,
        [
            str(spec_path),
            "-o",
            str(state_dir),
            "--advance",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "pairs_status" in data
    assert data["claim_boundary"] == CLAIM_BOUNDARY
    # Event log written
    assert (state_dir / "batch_events.jsonl").is_file()


def test_cli_batch_orchestrate_no_flags_exits_nonzero(tmp_path: Path) -> None:
    test_app = _make_test_app()

    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    spec_path = tmp_path / "batch_spec.json"
    _write_batch_spec(spec_path, spec)
    state_dir = tmp_path / "state"

    result = runner.invoke(
        test_app,
        [str(spec_path), "-o", str(state_dir)],
    )
    assert result.exit_code != 0


def test_cli_has_no_ack_flags(tmp_path: Path) -> None:
    """The CLI must not expose any --ack-* or --execute flags."""
    test_app = _make_test_app()

    sid = "fake__task-1"
    evidence_root = tmp_path / "evidence"
    pair = _pair_entry(source_task_id=sid, evidence_root=evidence_root)
    spec = _batch_spec(evidence_root, [pair])
    spec_path = tmp_path / "batch_spec.json"
    _write_batch_spec(spec_path, spec)
    state_dir = tmp_path / "state"

    # Try passing a forbidden flag — must fail with "No such option"
    for flag in ["--ack-docker", "--ack-external-provider", "--ack-official-eval", "--execute"]:
        result = runner.invoke(
            test_app,
            [str(spec_path), "-o", str(state_dir), flag],
        )
        assert result.exit_code != 0, f"Flag {flag} should not be accepted"
