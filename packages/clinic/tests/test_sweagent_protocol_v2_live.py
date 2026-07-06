from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_protocol_v2_live import (
    SWEAgentProtocolV2LiveSingleSpec,
    run_sweagent_protocol_v2_live_single,
)
from wutai_clinic.adapters.sweagent_protocol_v2_pair import (
    SWEAgentProtocolV2LivePairSpec,
    run_sweagent_protocol_v2_live_pair,
)
from wutai_clinic.adapters.sweagent_protocol_v2_official_eval import (
    SWEAgentProtocolV2OfficialEvalSpec,
    run_sweagent_protocol_v2_official_eval,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v2 import ProtocolV2, protocol_v2_prescription_template
from wutai_clinic.io import count_jsonl

runner = CliRunner()


class FakeAgent:
    def __init__(self):
        self.model = FakeModel()
        self.hooks = []

    def add_hook(self, hook):
        hook.on_init(agent=self)
        self.hooks.append(hook)


class FakeModel:
    def query(self, history):
        return {"message": "fake live output"}


class FakeRunSingle:
    def __init__(self, steps):
        self.agent = FakeAgent()
        self.steps = steps

    def run(self):
        for step in self.steps:
            for hook in self.agent.hooks:
                hook.on_action_started(step=step)
            for hook in self.agent.hooks:
                hook.on_action_executed(step=step)
        return {"ok": True}


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "run_single.yaml"
    config.write_text("agent:\n  model:\n    name: fake\n", encoding="utf-8")
    return config


def _protocol() -> ProtocolV2:
    return protocol_v2_prescription_template()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_protocol_v2_arm(
    root: Path,
    *,
    arm: str,
    replay_hashes: list[str],
    treatment_hook: bool = False,
    blocked_constraint: bool = False,
) -> Path:
    arm_dir = root / arm
    arm_dir.mkdir(parents=True)
    _write_json(
        arm_dir / "protocol_v2_live_single_report.json",
        {
            "decision": "protocol_v2_live_single_run_completed",
            "passed": True,
            "arm_type": arm,
            "source_task_id": "sympy__sympy-16281",
            "pair_id": "phase312_pair_010_failure_target_break_recurrence_and_replan",
            "replay_action_count": len(replay_hashes),
            "replay_config_hash": "same-replay",
            "constraint_blocked": blocked_constraint,
            "hook_event_count": 2 * len(replay_hashes) if treatment_hook else 0,
        },
    )
    _write_json(arm_dir / "protocol_v2_live_single_protocol.json", _protocol().to_dict())
    _write_json(arm_dir / "protocol_v2_live_single_replay_actions.json", ["a", "b", "c"])
    events = [
        {
            "event_type": "model_query",
            "query_index": index,
            "phase": "replay",
            "delegated": False,
            "output_sha256": replay_hash,
        }
        for index, replay_hash in enumerate(replay_hashes)
    ]
    events.append(
        {
            "event_type": "model_query",
            "query_index": len(replay_hashes),
            "phase": "generation",
            "delegated": True,
            "output_sha256": f"{arm}-live",
        }
    )
    if treatment_hook:
        for index in range(2 * len(replay_hashes)):
            events.append(
                {
                    "event_type": "protocol_v2_constraint",
                    "event": "protocol_v2_action_allowed",
                    "blocked": blocked_constraint and index == 0,
                    "action_index": index,
                }
            )
    (arm_dir / "protocol_v2_live_single_events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    return arm_dir


def test_protocol_v2_live_single_plans_without_running(tmp_path: Path) -> None:
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["python reproduce_failure.py"],
        ),
        policy=RuntimePermissionPolicy(),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_live_single_planned_no_run"
    assert report["arm_type"] == "treatment"
    assert report["run_single_started"] is False
    assert report["constraint_blocked"] is False
    assert report["replay_action_count"] == 1
    assert count_jsonl(result["events_path"]) == 0


def test_protocol_v2_live_single_treatment_enforces_prescription(tmp_path: Path) -> None:
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["python reproduce_failure.py"],
            execute=True,
            source_task_id="sympy__sympy-16281",
            pair_id="phase312_pair_010_failure_target_break_recurrence_and_replan",
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [
                {"action": "python reproduce_failure.py", "observation": "AssertionError: failed"},
                {"action": "rg AssertionError /testbed/sympy", "observation": "sympy/foo.py"},
                {"action": "str_replace_editor str_replace /testbed/sympy/foo.py"},
                {"action": "python reproduce_failure.py", "observation": "1 passed"},
                {"action": "submit"},
            ]
        ),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_live_single_run_completed"
    assert report["constraint_blocked"] is False
    assert report["hook_event_count"] == 10
    assert any(event["event"] == "protocol_v2_replay_action_allowed" for event in result["events"])


def test_protocol_v2_live_single_empty_replay_blocked_without_flag(tmp_path: Path) -> None:
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=[],
            arm_type="control",
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle([{"action": "submit"}]),
    )

    report = result["report"]
    assert report["decision"] == "protocol_v2_live_single_blocked_missing_replay_actions"
    assert report["run_single_started"] is False
    assert report["empty_replay_authorized"] is False


def test_protocol_v2_live_single_empty_replay_runs_with_flag(tmp_path: Path) -> None:
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=[],
            arm_type="control",
            execute=True,
            allow_empty_replay=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle([{"action": "submit"}]),
    )

    report = result["report"]
    assert report["decision"] == "protocol_v2_live_single_run_completed"
    assert report["run_single_started"] is True
    assert report["replay_action_count"] == 0
    assert report["empty_replay_authorized"] is True


def test_protocol_v2_live_single_observe_only_never_blocks(tmp_path: Path) -> None:
    # The first action violates require_explicit_failure_reproduction; in
    # enforce mode the run aborts, in observe-only it completes with a
    # would-block event recorded.
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["bash true"],
            execute=True,
            observe_only=True,
            source_task_id="sphinx-doc__sphinx-8474",
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [
                {"action": "bash true"},  # replay prefix
                {"action": "str_replace_editor str_replace /testbed/sphinx/foo.py"},
                {"action": "submit"},
            ]
        ),
    )

    report = result["report"]
    assert report["decision"] == "protocol_v2_live_single_run_completed"
    assert report["constraint_blocked"] is False
    assert report["observe_only"] is True
    assert report["would_block_count"] >= 1
    assert any(
        event["event"] == "protocol_v2_action_would_block_observe_only"
        for event in result["events"]
    )


def test_protocol_v2_live_single_control_arm_does_not_attach_constraint_hook(
    tmp_path: Path,
) -> None:
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["bash true"],
            arm_type="control",
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [{"action": "str_replace_editor str_replace /testbed/sympy/foo.py"}]
        ),
    )

    assert result["report"]["decision"] == "protocol_v2_live_single_run_completed"
    assert result["report"]["arm_type"] == "control"
    assert result["report"]["hook_event_count"] == 0


def test_cli_sweagent_protocol_v2_live_single_plans_package(tmp_path: Path) -> None:
    protocol_path = tmp_path / "protocol.json"
    replay_path = tmp_path / "replay.json"
    protocol_path.write_text(json.dumps(_protocol().to_dict(), indent=2, sort_keys=True) + "\n")
    replay_path.write_text(json.dumps(["python reproduce_failure.py"], indent=2) + "\n")
    output_dir = tmp_path / "cli-live-single"

    result = runner.invoke(
        app,
        [
            "sweagent-protocol-v2-live-single",
            str(_config(tmp_path)),
            "--protocol",
            str(protocol_path),
            "--replay-actions",
            str(replay_path),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "protocol_v2_live_single_planned_no_run"
    assert payload["arm_type"] == "treatment"
    assert payload["replay_action_count"] == 1
    assert (output_dir / "protocol_v2_live_single_report.json").exists()


def test_protocol_v2_live_pair_ready_pending_official_eval(tmp_path: Path) -> None:
    control_dir = _write_protocol_v2_arm(tmp_path, arm="control", replay_hashes=["a", "b", "c"])
    treatment_dir = _write_protocol_v2_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "b", "c"],
        treatment_hook=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")

    result = run_sweagent_protocol_v2_live_pair(
        spec=SWEAgentProtocolV2LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_live_pair_ready_pending_official_eval"
    assert report["state_capsule_equivalence_claimed"] is False
    assert result["pair_summary"]["behavior_control_type"] == "protocol_v2_constraint_hook"
    assert (tmp_path / "pair/control/sweagent_protocol_v2_live_single.patch").exists()


def test_protocol_v2_live_pair_records_blocked_constraint_without_blocking_handoff(
    tmp_path: Path,
) -> None:
    control_dir = _write_protocol_v2_arm(tmp_path, arm="control", replay_hashes=["a", "b"])
    treatment_dir = _write_protocol_v2_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "b"],
        treatment_hook=True,
        blocked_constraint=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")

    result = run_sweagent_protocol_v2_live_pair(
        spec=SWEAgentProtocolV2LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    assert result["report"]["passed"] is True
    assert result["report"]["decision"] == "protocol_v2_live_pair_ready_pending_official_eval"
    assert result["report"]["gates"]["treatment_constraint_outcome_recorded"] is True
    assert result["pair_summary"]["treatment_constraint_blocked"] is True


def test_protocol_v2_official_eval_writes_predictions_without_running_eval(
    tmp_path: Path,
) -> None:
    control_dir = _write_protocol_v2_arm(tmp_path, arm="control", replay_hashes=["a", "b", "c"])
    treatment_dir = _write_protocol_v2_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "b", "c"],
        treatment_hook=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")
    pair_result = run_sweagent_protocol_v2_live_pair(
        spec=SWEAgentProtocolV2LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    result = run_sweagent_protocol_v2_official_eval(
        spec=SWEAgentProtocolV2OfficialEvalSpec(
            pair_dir=pair_result["report_path"].parent,
            output_dir=tmp_path / "official",
            eval_dir=tmp_path / "isolated-eval",
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v2_official_eval_ready_pending_official_eval"
    assert report["official_eval_started"] is False
    assert report["official_eval_completed"] is False
    assert result["dual_scorecard"]["effect_label"] == "pending_or_incomplete"
    assert (tmp_path / "official/predictions/control_sympy__sympy-16281.jsonl").exists()
