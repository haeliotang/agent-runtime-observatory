from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live_preflight import (
    SWEAgentLiveHookPreflightSpec,
    run_sweagent_live_hook_preflight,
)
from wutai_clinic.cli import app
from wutai_clinic.intervention.batch_readiness import batch3_readiness_report
from wutai_clinic.intervention.stability import batch_stability_report
from wutai_clinic.io import count_jsonl, read_jsonl

from conftest import requires_monorepo

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"
runner = CliRunner()


class FakeCommandResponse:
    def __init__(self, *, stdout: str, exit_code: int | None = 0):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code


class FakeRuntime:
    def __init__(self, *, stdout: str = "clean diff"):
        self.stdout = stdout
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return FakeCommandResponse(stdout=self.stdout)


class FakeDeployment:
    def __init__(self, runtime: FakeRuntime):
        self.runtime = runtime


class FakeEnv:
    def __init__(self, runtime: FakeRuntime):
        self.deployment = FakeDeployment(runtime)


class FakeStats:
    def model_dump(self) -> dict[str, int]:
        return {"api_calls": 1}


class FakeModel:
    def __init__(self):
        self.calls = []
        self.stats = FakeStats()

    def query(self, history):
        self.calls.append([dict(message) for message in history])
        return {"message": "live generated action"}


class FakeAgent:
    def __init__(self):
        self.model = FakeModel()
        self.hooks = []

    def add_hook(self, hook):
        hook.on_init(agent=self)
        self.hooks.append(hook)


class FakeRunSingle:
    def __init__(self, *, runtime: FakeRuntime, agent_name: str):
        self.agent = FakeAgent()
        self.env = FakeEnv(runtime)
        self.agent_name = agent_name
        self.run_called = False

    def run(self):
        self.run_called = True
        replay_messages = []
        for _action in self.agent.model.replay_actions:
            for hook in self.agent.hooks:
                hook.on_model_query(messages=replay_messages, agent=self.agent_name)
            self.agent.model.query(replay_messages)
        live_messages = []
        for hook in self.agent.hooks:
            hook.on_model_query(messages=live_messages, agent=self.agent_name)
        self.agent.model.query(live_messages)
        return {"ok": True}


def _batch_rows() -> list[dict]:
    return [
        *read_jsonl(MODELS / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
        *read_jsonl(MODELS / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
    ]


def _readiness_report() -> dict:
    return batch3_readiness_report(
        stability_report=batch_stability_report(_batch_rows()),
        trigger_policy_review=json.loads(
            (MODELS / "phase316_trigger_policy_review_report.json").read_text()
        ),
        recalibration_report=json.loads(
            (MODELS / "phase316_live_trigger_recalibration_report.json").read_text()
        ),
        recalibration_protocol=json.loads(
            (MODELS / "phase316_live_trigger_recalibration_protocol.json").read_text()
        ),
        candidate_rows=_candidate_rows(),
        live_feature_dry_run_report=json.loads(
            (MODELS / "phase316_live_feature_hook_dry_run_report.json").read_text()
        ),
    )


def _phase63_readiness_report() -> dict:
    return {
        "decision": "phase63_low_nondeterminism_live_candidate_set_ready_for_offline_preflight",
        "passed": True,
        "continuation_policy": {
            "allow_live_hook_runner_preflight": True,
            "allow_batch3_real_run": False,
            "allow_external_provider_export": False,
            "allow_official_eval": False,
        },
    }


def _candidate_rows() -> list[dict]:
    return list(read_jsonl(MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl"))


def _phase63_candidate_rows() -> list[dict]:
    return [
        {
            "pair_id": "pair_low_nondeterminism",
            "source_task_id": "repo__low",
            "intervention_policy_id": "error_observation_recovery",
            "candidate_reason_codes": ["error_streak_or_error_observation"],
            "candidate_prefix_only_context": {"step_count": 3},
            "candidate_static_prefix_index": 3,
            "recalibrated_trigger_mode": "live_feature_signature_window",
            "exact_static_prefix_trigger_disabled": True,
            "batch3_real_run_authorized": False,
        }
    ]


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "run_single.yaml"
    config.write_text("agent:\n  model:\n    name: fake\n", encoding="utf-8")
    return config


def _replay(tmp_path: Path) -> Path:
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps([{"message": "replay action"}]) + "\n", encoding="utf-8")
    return replay


def test_live_hook_preflight_plan_writes_commands_without_running(tmp_path: Path) -> None:
    result = run_sweagent_live_hook_preflight(
        spec=SWEAgentLiveHookPreflightSpec(
            readiness_report=_readiness_report(),
            candidate_rows=_candidate_rows(),
            run_single_config=_config(tmp_path),
            output_dir=tmp_path / "out",
        ),
        policy=RuntimePermissionPolicy(),
    )

    report = result["report"]
    commands = json.loads(result["commands_path"].read_text())
    assert report["passed"] is True
    assert report["decision"] == "sweagent_live_hook_preflight_planned_no_run"
    assert report["execution_summary"]["control_started"] is False
    assert commands["requires_real_replay_actions_before_execute"] is True
    assert "sweagent-live-single" in commands["control"]
    assert "<REAL_REPLAY_ACTIONS_JSON>" in commands["control"]
    assert "--reference-capsule" in commands["treatment_after_control_capsule"]
    assert count_jsonl(result["candidate_path"]) == 1


def test_live_hook_preflight_execute_blocks_without_replay_actions(tmp_path: Path) -> None:
    called = False

    def factory(_path: Path):
        nonlocal called
        called = True
        raise AssertionError("factory should not be called when replay actions are absent")

    result = run_sweagent_live_hook_preflight(
        spec=SWEAgentLiveHookPreflightSpec(
            readiness_report=_readiness_report(),
            candidate_rows=_candidate_rows(),
            run_single_config=_config(tmp_path),
            output_dir=tmp_path / "out",
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=factory,
    )

    assert called is False
    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "sweagent_live_hook_preflight_blocked"
    assert result["report"]["gates"]["replay_actions_present_if_execute"] is False
    assert result["report"]["execution_summary"]["control_started"] is False


def test_live_hook_preflight_execute_with_fake_run_single_builds_pair_audit(
    tmp_path: Path,
) -> None:
    calls = []

    def factory(_path: Path):
        arm_name = "control_sweagent" if not calls else "treatment_sweagent"
        calls.append(arm_name)
        return FakeRunSingle(runtime=FakeRuntime(), agent_name=arm_name)

    result = run_sweagent_live_hook_preflight(
        spec=SWEAgentLiveHookPreflightSpec(
            readiness_report=_readiness_report(),
            candidate_rows=_candidate_rows(),
            run_single_config=_config(tmp_path),
            output_dir=tmp_path / "out",
            replay_actions_path=_replay(tmp_path),
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=factory,
    )

    report = result["report"]
    pair_report = result["pair_result"]["report"]
    assert calls == ["control_sweagent", "treatment_sweagent"]
    assert report["passed"] is True
    assert report["decision"] == "sweagent_live_hook_preflight_pair_ready_pending_official_eval"
    assert report["execution_summary"]["control_started"] is True
    assert report["execution_summary"]["treatment_started"] is True
    assert report["execution_summary"]["pair_decision"] == (
        "sweagent_live_pair_ready_pending_official_eval"
    )
    assert pair_report["passed"] is True
    assert pair_report["effect_label"] == "pending_or_incomplete"
    assert result["control_result"]["capsule_path"].exists()
    assert result["treatment_result"]["capsule_path"].exists()
    assert result["pair_result"]["summary_path"].exists()


@requires_monorepo
def test_live_hook_preflight_accepts_phase63_low_nondeterminism_readiness(
    tmp_path: Path,
) -> None:
    calls = []

    def factory(_path: Path):
        arm_name = "control_sweagent" if not calls else "treatment_sweagent"
        calls.append(arm_name)
        return FakeRunSingle(runtime=FakeRuntime(), agent_name=arm_name)

    result = run_sweagent_live_hook_preflight(
        spec=SWEAgentLiveHookPreflightSpec(
            readiness_report=_phase63_readiness_report(),
            candidate_rows=_phase63_candidate_rows(),
            run_single_config=_config(tmp_path),
            output_dir=tmp_path / "out",
            replay_actions_path=_replay(tmp_path),
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=factory,
    )

    report = result["report"]
    assert calls == ["control_sweagent", "treatment_sweagent"]
    assert report["passed"] is True
    assert report["decision"] == "sweagent_live_hook_preflight_pair_ready_pending_official_eval"
    assert report["gates"]["readiness_decision_allows_live_hook_preflight"] is True
    assert report["source_task_id"] == "repo__low"


def test_cli_batch3_live_hook_preflight_plans_package(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps(_readiness_report(), indent=2, sort_keys=True) + "\n")
    output_dir = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "batch3-live-hook-preflight",
            str(readiness),
            str(MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl"),
            str(_config(tmp_path)),
            "-o",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["decision"] == "sweagent_live_hook_preflight_planned_no_run"
    assert payload["control_started"] is False
    assert (output_dir / "live_hook_preflight_report.json").exists()
    assert (output_dir / "live_hook_preflight_commands.json").exists()
