from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_protocol_v1_live import (
    SWEAgentProtocolV1LiveSingleSpec,
    run_sweagent_protocol_v1_live_single,
)
from wutai_clinic.adapters.sweagent_protocol_v1_runtime import (
    SWEAgentProtocolV1RuntimeConfigSpec,
    activate_sweagent_protocol_v1_runtime_config,
)
from wutai_clinic.adapters.sweagent_protocol_v1_pair import (
    SWEAgentProtocolV1LivePairSpec,
    run_sweagent_protocol_v1_live_pair,
)
from wutai_clinic.adapters.sweagent_protocol_v1_official_eval import (
    SWEAgentProtocolV1OfficialEvalSpec,
    run_sweagent_protocol_v1_official_eval,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.protocol_v1 import (
    ProtocolV1,
    protocol_v1_for_no_uplift_classification,
)
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
    def __init__(self):
        self.calls = []

    def query(self, history):
        self.calls.append(list(history))
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


def _json_config(tmp_path: Path) -> Path:
    config = tmp_path / "run_single.json"
    config.write_text(
        json.dumps(
            {
                "agent": {
                    "model": {
                        "name": "openai/gpt-5.5",
                        "api_key": "sk-do-not-write",
                        "api_base": None,
                        "per_instance_call_limit": 0,
                        "per_instance_cost_limit": 0.0,
                        "total_cost_limit": 0.0,
                    }
                },
                "output_dir": "/tmp/shared-native-output",
                "problem_statement": {"id": "matplotlib__matplotlib-25079"},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _protocol() -> ProtocolV1:
    return protocol_v1_for_no_uplift_classification(
        classification="behavior_diverged_but_target_failure_persisted",
        trigger_predicate="error_streak >= 1",
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_protocol_v1_arm(
    root: Path,
    *,
    arm: str,
    replay_hashes: list[str],
    treatment_hook: bool = False,
) -> Path:
    arm_dir = root / arm
    arm_dir.mkdir(parents=True)
    _write_json(
        arm_dir / "protocol_v1_live_single_report.json",
        {
            "decision": "protocol_v1_live_single_run_completed",
            "passed": True,
            "arm_type": arm,
            "source_task_id": "matplotlib__matplotlib-25079",
            "pair_id": "pair-018",
            "replay_action_count": len(replay_hashes),
            "replay_config_hash": "same-replay",
            "constraint_blocked": False,
            "hook_event_count": 2 * len(replay_hashes) if treatment_hook else 0,
        },
    )
    _write_json(arm_dir / "protocol_v1_live_single_protocol.json", _protocol().to_dict())
    _write_json(arm_dir / "protocol_v1_live_single_replay_actions.json", ["a", "b", "c"])
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
                    "event_type": "protocol_v1_constraint",
                    "event": "protocol_v1_action_allowed",
                    "blocked": False,
                    "action_index": index,
                }
            )
    (arm_dir / "protocol_v1_live_single_events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    return arm_dir


def test_protocol_v1_live_single_plans_without_running(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=config_path,
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["python reproduce_failure.py"],
        ),
        policy=RuntimePermissionPolicy(),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_live_single_planned_no_run"
    assert report["arm_type"] == "treatment"
    assert report["run_single_started"] is False
    assert report["constraint_blocked"] is False
    assert report["continuation_policy"]["allow_pair_assembly"] is False
    assert report["replay_action_count"] == 1
    assert count_jsonl(result["events_path"]) == 0
    artifact_paths = {Path(row["path"]).name for row in result["manifest"]["artifacts"]}
    assert config_path.name in artifact_paths
    assert result["replay_path"].exists()


def test_protocol_v1_live_single_fake_run_blocks_edit_before_reproduction(
    tmp_path: Path,
) -> None:
    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["bash true"],
            execute=True,
            source_task_id="pytest-dev__pytest-8365",
            pair_id="pair-targeted",
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [
                {"action": "bash true", "observation": "ok"},
                {"action": "str_replace_editor str_replace /testbed/src/pkg.py"},
            ]
        ),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_live_single_constraint_blocked"
    assert report["run_single_started"] is True
    assert report["constraint_blocked"] is True
    assert report["constraint_block_event"]["constraint_id"] == (
        "block_edit_until_failure_reproduced_or_explained"
    )
    assert count_jsonl(result["events_path"]) == 3


def test_protocol_v1_live_single_replay_prefix_can_materialize_failure(
    tmp_path: Path,
) -> None:
    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["python reproduce_failure.py"],
            execute=True,
            source_task_id="pytest-dev__pytest-8365",
            pair_id="pair-targeted",
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [
                {
                    "action": "python reproduce_failure.py",
                    "observation": "Traceback ... AssertionError: failed",
                },
                {"action": "str_replace_editor str_replace /testbed/src/pkg.py"},
            ]
        ),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_live_single_run_completed"
    assert report["constraint_blocked"] is False
    assert report["run_single_started"] is True
    assert report["hook_event_count"] == 4
    assert any(event["event"] == "protocol_v1_replay_action_allowed" for event in result["events"])


def test_protocol_v1_live_single_control_arm_does_not_attach_constraint_hook(
    tmp_path: Path,
) -> None:
    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["bash true"],
            arm_type="control",
            execute=True,
            source_task_id="pytest-dev__pytest-8365",
            pair_id="pair-targeted",
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            [{"action": "str_replace_editor str_replace /testbed/src/pkg.py"}]
        ),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_live_single_run_completed"
    assert report["arm_type"] == "control"
    assert report["hook_event_count"] == 0
    assert report["constraint_blocked"] is False


def test_protocol_v1_live_single_blocks_execute_without_ack(tmp_path: Path) -> None:
    called = False

    def factory(_path):
        nonlocal called
        called = True
        return FakeRunSingle([])

    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            replay_actions=["python reproduce_failure.py"],
            execute=True,
        ),
        policy=RuntimePermissionPolicy(),
        run_single_factory=factory,
    )

    assert called is False
    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "protocol_v1_live_single_blocked_needs_ack"


def test_protocol_v1_live_single_blocks_execute_without_replay_actions(tmp_path: Path) -> None:
    called = False

    def factory(_path):
        nonlocal called
        called = True
        return FakeRunSingle([])

    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            protocol=_protocol(),
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=factory,
    )

    assert called is False
    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "protocol_v1_live_single_blocked_missing_replay_actions"


def test_cli_sweagent_protocol_v1_live_single_plans_package(tmp_path: Path) -> None:
    protocol_path = tmp_path / "protocol.json"
    replay_path = tmp_path / "replay.json"
    protocol_path.write_text(json.dumps(_protocol().to_dict(), indent=2, sort_keys=True) + "\n")
    replay_path.write_text(json.dumps(["python reproduce_failure.py"], indent=2) + "\n")
    output_dir = tmp_path / "cli-live-single"

    result = runner.invoke(
        app,
        [
            "sweagent-protocol-v1-live-single",
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
    assert payload["decision"] == "protocol_v1_live_single_planned_no_run"
    assert payload["arm_type"] == "treatment"
    assert payload["run_single_started"] is False
    assert payload["replay_action_count"] == 1
    assert (output_dir / "protocol_v1_live_single_report.json").exists()


def test_protocol_v1_runtime_config_activation_strips_secret_and_sets_budget(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "runtime" / "control"
    native_output_dir = tmp_path / "native" / "control"
    result = activate_sweagent_protocol_v1_runtime_config(
        SWEAgentProtocolV1RuntimeConfigSpec(
            config_path=_json_config(tmp_path),
            output_dir=output_dir,
            arm_type="control",
            native_output_dir=native_output_dir,
            api_base="https://proxy.example.test/v1",
            per_instance_call_limit=7,
            source_task_id="matplotlib__matplotlib-25079",
            pair_id="pair-018",
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_runtime_config_ready"
    assert report["api_key_field_present_in_input"] is True
    assert report["provider_env_contract"]["secrets_persisted"] is False
    activated = json.loads(result["config_path"].read_text())
    model = activated["agent"]["model"]
    assert model["api_key"] is None
    assert model["api_base"] == "https://proxy.example.test/v1"
    assert model["per_instance_call_limit"] == 7
    assert model["per_instance_cost_limit"] == 0.0
    assert model["total_cost_limit"] == 0.0
    assert activated["output_dir"] == native_output_dir.resolve().as_posix()
    assert "sk-do-not-write" not in result["config_path"].read_text()


def test_protocol_v1_runtime_config_activation_rejects_unscoped_output_dir(
    tmp_path: Path,
) -> None:
    result = activate_sweagent_protocol_v1_runtime_config(
        SWEAgentProtocolV1RuntimeConfigSpec(
            config_path=_json_config(tmp_path),
            output_dir=tmp_path / "runtime" / "control",
            arm_type="control",
            native_output_dir=tmp_path / "native" / "shared",
        )
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "protocol_v1_runtime_config_not_ready"
    assert "arm_scoped_native_output_dir" in result["report"]["blocking_failures"]


def test_cli_protocol_v1_runtime_config_activation(tmp_path: Path) -> None:
    output_dir = tmp_path / "runtime" / "treatment"
    native_output_dir = tmp_path / "native" / "treatment"
    result = runner.invoke(
        app,
        [
            "sweagent-protocol-v1-activate-runtime-config",
            str(_json_config(tmp_path)),
            "-o",
            str(output_dir),
            "--arm",
            "treatment",
            "--native-output-dir",
            str(native_output_dir),
            "--api-base",
            "https://proxy.example.test/v1",
            "--per-instance-call-limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "protocol_v1_runtime_config_ready"
    assert payload["arm_type"] == "treatment"
    assert payload["per_instance_call_limit"] == 5
    assert (output_dir / "protocol_v1_runtime_config.json").exists()


def test_protocol_v1_live_pair_ready_pending_official_eval(tmp_path: Path) -> None:
    control_dir = _write_protocol_v1_arm(tmp_path, arm="control", replay_hashes=["a", "b", "c"])
    treatment_dir = _write_protocol_v1_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "b", "c"],
        treatment_hook=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")

    result = run_sweagent_protocol_v1_live_pair(
        spec=SWEAgentProtocolV1LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_live_pair_ready_pending_official_eval"
    assert report["state_capsule_equivalence_claimed"] is False
    assert result["pair_summary"]["replay_prefix_output_hashes_match"] is True
    assert (tmp_path / "pair/control/sweagent_protocol_v1_live_single.patch").exists()


def test_protocol_v1_live_pair_blocks_replay_prefix_mismatch(tmp_path: Path) -> None:
    control_dir = _write_protocol_v1_arm(tmp_path, arm="control", replay_hashes=["a", "b", "c"])
    treatment_dir = _write_protocol_v1_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "DIFFERENT", "c"],
        treatment_hook=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")

    result = run_sweagent_protocol_v1_live_pair(
        spec=SWEAgentProtocolV1LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "protocol_v1_live_pair_blocked"
    assert "replay_prefix_output_hashes_match" in result["report"]["blocking_failures"]


def test_protocol_v1_official_eval_writes_predictions_without_running_eval(
    tmp_path: Path,
) -> None:
    control_dir = _write_protocol_v1_arm(tmp_path, arm="control", replay_hashes=["a", "b", "c"])
    treatment_dir = _write_protocol_v1_arm(
        tmp_path,
        arm="treatment",
        replay_hashes=["a", "b", "c"],
        treatment_hook=True,
    )
    control_patch = tmp_path / "control.patch"
    treatment_patch = tmp_path / "treatment.patch"
    control_patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    treatment_patch.write_text("diff --git a/b b/b\n", encoding="utf-8")
    pair_result = run_sweagent_protocol_v1_live_pair(
        spec=SWEAgentProtocolV1LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=tmp_path / "pair",
            control_patch=control_patch,
            treatment_patch=treatment_patch,
        )
    )

    result = run_sweagent_protocol_v1_official_eval(
        spec=SWEAgentProtocolV1OfficialEvalSpec(
            pair_dir=pair_result["report_path"].parent,
            output_dir=tmp_path / "official",
            eval_dir=tmp_path / "isolated-eval",
        )
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["decision"] == "protocol_v1_official_eval_ready_pending_official_eval"
    assert report["official_eval_started"] is False
    assert report["official_eval_completed"] is False
    assert result["dual_scorecard"]["effect_label"] == "pending_or_incomplete"
    assert (tmp_path / "official/predictions/control_matplotlib__matplotlib-25079.jsonl").exists()
