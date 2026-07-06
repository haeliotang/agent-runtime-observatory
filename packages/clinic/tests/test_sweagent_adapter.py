from __future__ import annotations

import json
from pathlib import Path

from wutai_clinic.adapters.base import ForkArmRequest, RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent import (
    MappingReadOnlyProbe,
    SWEAgentCapsuleConfig,
    SWEAgentCapsuleExtractor,
    SWEAgentForkRunner,
    SWEAgentRunSingleAdapter,
    SWEEnvRuntimeProbe,
    run_sweagent_fork_preflight,
    sweagent_live_plan_report,
)
from wutai_clinic.intervention.hybrid_runner import CapsuleBuildContext, message_prefix_hash
from wutai_clinic.intervention.paired_fork import (
    default_generation_messages,
    default_protocol,
    default_replay_actions,
)
from wutai_clinic.io import count_jsonl

from conftest import requires_swerex


class FakeCommandResponse:
    def __init__(self, *, stdout: str, stderr: str = "", exit_code: int | None = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class FakeRuntime:
    def __init__(self, *, stdout: str = "runtime stdout", exit_code: int | None = 0):
        self.stdout = stdout
        self.exit_code = exit_code
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return FakeCommandResponse(stdout=self.stdout, exit_code=self.exit_code)


class FakeEnv:
    def __init__(self, *, runtime: FakeRuntime, repo_name: str | None = None):
        self.deployment = type("FakeDeployment", (), {"runtime": runtime})()
        self.repo = type("FakeRepo", (), {"repo_name": repo_name})() if repo_name else None

    def communicate(self, *_args, **_kwargs):
        raise AssertionError("runtime probe must not use SWEEnv.communicate")


class FakeLiveStats:
    def model_dump(self) -> dict[str, int]:
        return {"api_calls": 1}


class FakeLiveModel:
    def __init__(self):
        self.calls = []
        self.stats = FakeLiveStats()

    def query(self, history):
        self.calls.append([dict(message) for message in history])
        return {"message": "live generated action"}


class FakeAgent:
    def __init__(self):
        self.model = FakeLiveModel()
        self.hooks = []

    def add_hook(self, hook):
        hook.on_init(agent=self)
        self.hooks.append(hook)


class FakeRunSingle:
    def __init__(self, *, runtime: FakeRuntime, repo_name: str | None = None):
        self.agent = FakeAgent()
        self.env = FakeEnv(runtime=runtime, repo_name=repo_name)


def test_sweagent_capsule_extractor_hashes_probe_output_without_raw_payload() -> None:
    probe = MappingReadOnlyProbe({"git diff --binary --no-ext-diff": "secret raw diff"})
    extractor = SWEAgentCapsuleExtractor(
        probe=probe,
        config=SWEAgentCapsuleConfig(observation_window=["obs-1", "obs-2"]),
    )
    messages = [{"role": "user", "content": "task"}]

    capsule = extractor.build(
        CapsuleBuildContext(
            arm_type="control",
            agent_name="main",
            query_index=1,
            messages=messages,
        )
    )
    payload = json.dumps(capsule.to_dict(), sort_keys=True)

    assert capsule.message_prefix_hash == message_prefix_hash(messages)
    assert "secret raw diff" not in payload
    assert capsule.metadata["raw_probe_payload_logged"] is False
    assert capsule.metadata["adapter"] == "sweagent"


@requires_swerex
def test_swe_env_runtime_probe_uses_runtime_execute_not_interactive_session() -> None:
    runtime = FakeRuntime(stdout="diff output")
    env = FakeEnv(runtime=runtime)
    probe = SWEEnvRuntimeProbe(env=env)

    output = probe.capture("git diff --binary --no-ext-diff", cwd="/repo")

    assert output == "diff output"
    assert len(runtime.commands) == 1
    command = runtime.commands[0]
    assert command.command == "git diff --binary --no-ext-diff"
    assert command.cwd == "/repo"
    assert command.shell is True
    assert command.check is False


@requires_swerex
def test_swe_env_runtime_probe_raises_on_nonzero_exit() -> None:
    probe = SWEEnvRuntimeProbe(env=FakeEnv(runtime=FakeRuntime(exit_code=2)))

    try:
        probe.capture("git diff")
    except RuntimeError as exc:
        assert "exit_code=2" in str(exc)
    else:
        raise AssertionError("expected failed runtime probe to raise")


def test_sweagent_fork_runner_preflight_injects_only_treatment_after_equivalence() -> None:
    protocol = default_protocol()
    replay_actions = default_replay_actions()
    generation_messages = default_generation_messages()
    runner = SWEAgentForkRunner()
    control = runner.run_arm(
        ForkArmRequest(
            arm_type="control",
            protocol=protocol,
            replay_actions=replay_actions,
            generation_messages=generation_messages,
            features={"error_streak": 3},
        )
    )
    treatment = runner.run_arm(
        ForkArmRequest(
            arm_type="treatment",
            protocol=protocol,
            replay_actions=replay_actions,
            generation_messages=generation_messages,
            features={"error_streak": 3},
            reference_capsule=control.capsule,
        )
    )

    assert control.injection_count == 0
    assert treatment.injection_count == 1
    assert treatment.trigger_hit is True
    assert control.delegate_call_count == 1
    assert treatment.delegate_call_count == 1
    assert [event["phase"] for event in treatment.model_events] == ["replay", "generation"]
    assert treatment.hook_events[0]["fork_passed"] is True


def test_sweagent_fork_runner_rejects_live_permission_policy() -> None:
    runner = SWEAgentForkRunner(policy=RuntimePermissionPolicy(allow_external_provider=True))

    try:
        runner.run_arm(
            ForkArmRequest(
                arm_type="control",
                protocol=default_protocol(),
                replay_actions=[],
                generation_messages=[],
            )
        )
    except RuntimeError as exc:
        assert "does not execute live runtimes" in str(exc)
    else:
        raise AssertionError("expected live policy to be rejected")


def test_sweagent_fork_preflight_writes_ready_package(tmp_path: Path) -> None:
    result = run_sweagent_fork_preflight(output_dir=tmp_path)
    report = result["report"]
    manifest = result["manifest"]

    assert report["passed"] is True
    assert report["decision"] == "sweagent_adapter_preflight_ready_no_real_run"
    assert report["effect_label"] == "pending_or_incomplete"
    assert report["gates"]["external_provider_not_called"] is True
    assert report["gates"]["docker_not_started"] is True
    assert report["gates"]["official_eval_not_claimed"] is True
    assert count_jsonl(result["events_path"]) == 6
    assert manifest["passed"] is True
    assert len(manifest["artifacts"]) == 5


def test_sweagent_fork_preflight_blocks_capsule_mismatch(tmp_path: Path) -> None:
    result = run_sweagent_fork_preflight(
        output_dir=tmp_path,
        treatment_capsule_overrides={"model_request_hash": "different"},
    )
    report = result["report"]

    assert report["passed"] is False
    assert report["decision"] == "sweagent_adapter_preflight_blocked"
    assert report["effect_label"] == "state_mismatch_no_attribution"
    assert report["fork_equivalence"]["mismatched_fields"] == ["model_request_hash"]


def test_sweagent_run_single_adapter_requires_live_authorization() -> None:
    adapter = SWEAgentRunSingleAdapter(policy=RuntimePermissionPolicy(allow_docker=True))
    request = ForkArmRequest(
        arm_type="control",
        protocol=default_protocol(),
        replay_actions=[],
        generation_messages=[],
    )

    try:
        adapter.attach(run_single=FakeRunSingle(runtime=FakeRuntime()), request=request)
    except PermissionError as exc:
        assert "allow_external_provider" in str(exc)
    else:
        raise AssertionError("expected missing provider acknowledgement to be rejected")


@requires_swerex
def test_sweagent_run_single_adapter_wraps_model_and_materializes_runtime_capsule() -> None:
    runtime = FakeRuntime(stdout="clean diff")
    run_single = FakeRunSingle(runtime=runtime)
    protocol = default_protocol()
    replay_actions = default_replay_actions()
    generation_messages = default_generation_messages()
    capsule_config = SWEAgentCapsuleConfig(observation_window=["obs"])
    reference_capsule = SWEAgentCapsuleExtractor(
        probe=MappingReadOnlyProbe({"git diff --binary --no-ext-diff": "clean diff"}),
        config=capsule_config,
    ).build(
        CapsuleBuildContext(
            arm_type="treatment",
            agent_name="treatment_sweagent",
            query_index=len(replay_actions),
            messages=generation_messages,
        )
    )
    adapter = SWEAgentRunSingleAdapter(
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True)
    )

    attachment = adapter.attach(
        run_single=run_single,
        request=ForkArmRequest(
            arm_type="treatment",
            protocol=protocol,
            replay_actions=replay_actions,
            generation_messages=generation_messages,
            features={"error_streak": 3},
            reference_capsule=reference_capsule,
        ),
        capsule_config=capsule_config,
    )

    assert run_single.agent.model is attachment.hybrid_model
    assert run_single.agent.hooks == [attachment.hook]
    replay_messages = []
    attachment.hook.on_model_query(messages=replay_messages, agent="treatment_sweagent")
    run_single.agent.model.query(replay_messages)
    live_messages = [dict(message) for message in generation_messages]
    attachment.hook.on_model_query(messages=live_messages, agent="treatment_sweagent")
    output = run_single.agent.model.query(live_messages)

    assert output == {"message": "live generated action"}
    assert attachment.hook.injection_count == 1
    assert attachment.hook.capsule is not None
    assert attachment.hook.safe_audit_events[0]["fork_passed"] is True
    assert len(runtime.commands) == 1
    assert attachment.original_model.calls[0][-1]["role"] == "system"
    attachment.restore(run_single)
    assert run_single.agent.model is attachment.original_model


@requires_swerex
def test_sweagent_run_single_adapter_infers_repo_cwd_for_runtime_probe() -> None:
    runtime = FakeRuntime(stdout="clean diff")
    run_single = FakeRunSingle(runtime=runtime, repo_name="SWE-agent__test-repo")
    adapter = SWEAgentRunSingleAdapter(
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True)
    )
    attachment = adapter.attach(
        run_single=run_single,
        request=ForkArmRequest(
            arm_type="control",
            protocol=default_protocol(),
            replay_actions=[],
            generation_messages=[],
            features={"error_streak": 3},
        ),
    )

    messages = [{"role": "user", "content": "task"}]
    attachment.hook.on_model_query(messages=messages, agent="control_sweagent")

    assert attachment.hook.capsule is not None
    assert runtime.commands[0].cwd == "/SWE-agent__test-repo"


def test_sweagent_live_plan_report_blocks_until_required_acks() -> None:
    blocked = sweagent_live_plan_report(policy=RuntimePermissionPolicy())
    allowed = sweagent_live_plan_report(
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True)
    )

    assert blocked["passed"] is False
    assert blocked["decision"] == "sweagent_run_single_live_blocked_needs_ack"
    assert blocked["gates"]["requires_docker_ack"] is False
    assert allowed["passed"] is True
    assert allowed["decision"] == "sweagent_run_single_live_authorized"
