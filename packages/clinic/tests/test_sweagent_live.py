from __future__ import annotations

from pathlib import Path

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent import (
    MappingReadOnlyProbe,
    SWEAgentCapsuleConfig,
    SWEAgentCapsuleExtractor,
)
from wutai_clinic.adapters.sweagent_live import SWEAgentLiveSingleSpec, run_sweagent_live_single
from wutai_clinic.intervention.hybrid_runner import CapsuleBuildContext
from wutai_clinic.intervention.paired_fork import (
    default_generation_messages,
    default_protocol,
    default_replay_actions,
)
from wutai_clinic.intervention.replay_protocol import StateCapsule, verify_fork_equivalence
from wutai_clinic.io import count_jsonl

from conftest import requires_monorepo


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
    def __init__(self, *, runtime: FakeRuntime, agent_name: str, live_messages):
        self.agent = FakeAgent()
        self.env = FakeEnv(runtime)
        self.agent_name = agent_name
        self.live_messages = [dict(message) for message in live_messages]
        self.run_called = False

    def run(self):
        self.run_called = True
        replay_messages = []
        for _action in self.agent.model.replay_actions:
            for hook in self.agent.hooks:
                hook.on_model_query(messages=replay_messages, agent=self.agent_name)
            self.agent.model.query(replay_messages)
        live_messages = [dict(message) for message in self.live_messages]
        for hook in self.agent.hooks:
            hook.on_model_query(messages=live_messages, agent=self.agent_name)
        self.agent.model.query(live_messages)
        return {"ok": True}


class PatchWritingFakeRunSingle(FakeRunSingle):
    def __init__(
        self,
        *,
        runtime: FakeRuntime,
        agent_name: str,
        live_messages,
        native_output_dir: Path,
        source_task_id: str,
    ):
        super().__init__(
            runtime=runtime,
            agent_name=agent_name,
            live_messages=live_messages,
        )
        self.native_output_dir = native_output_dir
        self.source_task_id = source_task_id

    def run(self):
        result = super().run()
        task_dir = self.native_output_dir / self.source_task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / f"{self.source_task_id}.patch").write_text(
            "diff --git a/example.py b/example.py\n",
            encoding="utf-8",
        )
        (task_dir / f"{self.source_task_id}.pred").write_text(
            '{"model_patch":"diff --git a/example.py b/example.py\\n"}\n',
            encoding="utf-8",
        )
        (task_dir / f"{self.source_task_id}.traj").write_text("{}\n", encoding="utf-8")
        return result


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "run_single.yaml"
    config.write_text("agent:\n  model:\n    name: fake\n", encoding="utf-8")
    return config


def _config_with_native_output(tmp_path: Path, *, source_task_id: str, output_dir: Path) -> Path:
    config = tmp_path / "run_single_with_output.yaml"
    config.write_text(
        "\n".join(
            [
                "agent:",
                "  model:",
                "    name: fake",
                f"output_dir: {output_dir.as_posix()}",
                "problem_statement:",
                f"  id: {source_task_id}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_sweagent_live_single_plan_does_not_call_factory(tmp_path: Path) -> None:
    called = False

    def factory(_path: Path):
        nonlocal called
        called = True
        raise AssertionError("factory should not be called in plan mode")

    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(config_path=_config(tmp_path), output_dir=tmp_path / "out"),
        policy=RuntimePermissionPolicy(),
        run_single_factory=factory,
    )

    assert called is False
    assert result["report"]["passed"] is True
    assert result["report"]["decision"] == "sweagent_live_single_planned_no_run"
    assert result["report"]["run_single_started"] is False
    assert count_jsonl(result["events_path"]) == 0


def test_sweagent_live_single_execute_without_ack_does_not_call_factory(tmp_path: Path) -> None:
    called = False

    def factory(_path: Path):
        nonlocal called
        called = True
        raise AssertionError("factory should not be called without live acknowledgement")

    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            execute=True,
        ),
        policy=RuntimePermissionPolicy(),
        run_single_factory=factory,
    )

    assert called is False
    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "sweagent_live_single_blocked_needs_ack"
    assert result["report"]["run_single_started"] is False


def test_sweagent_live_single_blocks_treatment_execute_without_reference(tmp_path: Path) -> None:
    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            arm_type="treatment",
            execute=True,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="treatment_sweagent",
            live_messages=default_generation_messages(),
        ),
    )

    assert result["report"]["passed"] is False
    assert result["report"]["decision"] == "sweagent_live_single_blocked_missing_reference_capsule"
    assert result["report"]["run_single_started"] is False


@requires_monorepo
def test_sweagent_live_single_execute_with_fake_run_single_materializes_capsule(
    tmp_path: Path,
) -> None:
    live_messages = default_generation_messages()
    capsule_config = SWEAgentCapsuleConfig(observation_window=["obs"])
    reference = SWEAgentCapsuleExtractor(
        probe=MappingReadOnlyProbe({"git diff --binary --no-ext-diff": "clean diff"}),
        config=capsule_config,
    ).build(
        CapsuleBuildContext(
            arm_type="treatment",
            agent_name="treatment_sweagent",
            query_index=len(default_replay_actions()),
            messages=live_messages,
        )
    )

    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            arm_type="treatment",
            execute=True,
            protocol=default_protocol(),
            replay_actions=default_replay_actions(),
            features={"error_streak": 3},
            reference_capsule=reference,
            capsule_config=capsule_config,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="treatment_sweagent",
            live_messages=live_messages,
        ),
    )

    assert result["report"]["passed"] is True
    assert result["report"]["decision"] == "sweagent_live_single_run_completed"
    assert result["report"]["run_single_started"] is True
    assert result["report"]["capsule_fingerprint"] is not None
    assert result["report"]["injection_count"] == 1
    assert result["capsule_path"] is not None
    assert result["capsule_path"].exists()
    assert count_jsonl(result["events_path"]) == 3


@requires_monorepo
def test_sweagent_live_single_control_execute_allows_empty_replay_prefix(
    tmp_path: Path,
) -> None:
    live_messages = default_generation_messages()
    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=_config(tmp_path),
            output_dir=tmp_path / "out",
            arm_type="control",
            execute=True,
            replay_actions=[],
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="control_sweagent",
            live_messages=live_messages,
        ),
    )

    assert result["report"]["passed"] is True
    assert result["report"]["decision"] == "sweagent_live_single_run_completed"
    assert result["report"]["run_single_started"] is True
    assert result["report"]["replay_action_count"] == 0
    assert result["report"]["capsule_fingerprint"] is not None


@requires_monorepo
def test_sweagent_live_single_archives_native_patch_artifacts(tmp_path: Path) -> None:
    live_messages = default_generation_messages()
    source_task_id = "django__django-14667"
    native_output_dir = tmp_path / "native"
    config = _config_with_native_output(
        tmp_path,
        source_task_id=source_task_id,
        output_dir=native_output_dir,
    )

    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=config,
            output_dir=tmp_path / "out",
            arm_type="control",
            execute=True,
            replay_actions=[],
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: PatchWritingFakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="control_sweagent",
            live_messages=live_messages,
            native_output_dir=native_output_dir,
            source_task_id=source_task_id,
        ),
    )

    report = result["report"]
    assert report["passed"] is True
    assert report["patch_archived"] is True
    assert report["patch_source_kind"] == "native_patch_file"
    assert report["source_task_id"] == source_task_id
    assert (
        (tmp_path / "out" / "sweagent_live_single.patch")
        .read_text(encoding="utf-8")
        .startswith("diff --git")
    )
    assert (tmp_path / "out" / "sweagent_live_single.pred").exists()
    assert (tmp_path / "out" / "sweagent_live_single.traj").exists()


@requires_monorepo
def test_sweagent_live_single_treatment_execute_allows_empty_equivalent_replay_prefix(
    tmp_path: Path,
) -> None:
    live_messages = default_generation_messages()
    config = _config(tmp_path)
    control = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=config,
            output_dir=tmp_path / "control",
            arm_type="control",
            execute=True,
            replay_actions=[],
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="control_sweagent",
            live_messages=live_messages,
        ),
    )
    control_capsule = StateCapsule.from_file(control["capsule_path"])

    treatment = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=config,
            output_dir=tmp_path / "treatment",
            arm_type="treatment",
            execute=True,
            replay_actions=[],
            reference_capsule=control_capsule,
        ),
        policy=RuntimePermissionPolicy(allow_docker=True, allow_external_provider=True),
        run_single_factory=lambda _path: FakeRunSingle(
            runtime=FakeRuntime(),
            agent_name="treatment_sweagent",
            live_messages=live_messages,
        ),
    )
    treatment_capsule = StateCapsule.from_file(treatment["capsule_path"])
    fork = verify_fork_equivalence(control_capsule, treatment_capsule)

    assert treatment["report"]["passed"] is True
    assert treatment["report"]["decision"] == "sweagent_live_single_run_completed"
    assert treatment["report"]["run_single_started"] is True
    assert treatment["report"]["replay_action_count"] == 0
    assert treatment["report"]["injection_count"] == 1
    assert fork["passed"] is True
