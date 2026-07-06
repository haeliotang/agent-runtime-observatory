from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from wutai_clinic.intervention.hybrid_runner import ArmType
from wutai_clinic.intervention.replay_protocol import InterventionProtocol, StateCapsule


class ReadOnlyProbe(Protocol):
    """Read runtime state without mutating the agent session."""

    def capture(self, command: str, *, cwd: str | None = None) -> str: ...


@dataclass(frozen=True)
class RuntimePermissionPolicy:
    allow_docker: bool = False
    allow_external_provider: bool = False
    allow_official_eval: bool = False

    def allows(
        self,
        *,
        require_docker: bool = False,
        require_external_provider: bool = False,
        require_official_eval: bool = False,
    ) -> bool:
        return (
            (not require_docker or self.allow_docker)
            and (not require_external_provider or self.allow_external_provider)
            and (not require_official_eval or self.allow_official_eval)
        )

    def assert_allows(
        self,
        *,
        require_docker: bool = False,
        require_external_provider: bool = False,
        require_official_eval: bool = False,
    ) -> None:
        missing = []
        if require_docker and not self.allow_docker:
            missing.append("allow_docker")
        if require_external_provider and not self.allow_external_provider:
            missing.append("allow_external_provider")
        if require_official_eval and not self.allow_official_eval:
            missing.append("allow_official_eval")
        if missing:
            raise PermissionError(f"live runtime authorization missing: {', '.join(missing)}")

    def gate_results(self) -> dict[str, bool]:
        return {
            "external_provider_not_called": not self.allow_external_provider,
            "docker_not_started": not self.allow_docker,
            "official_eval_not_claimed": not self.allow_official_eval,
        }


@dataclass
class ForkArmRequest:
    arm_type: ArmType
    protocol: InterventionProtocol
    replay_actions: list[dict[str, Any] | str]
    generation_messages: list[dict[str, Any]]
    features: dict[str, Any] = field(default_factory=dict)
    reference_capsule: StateCapsule | None = None
    capsule_overrides: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ForkArmResult:
    arm_type: ArmType
    capsule: StateCapsule
    model_events: list[dict[str, Any]]
    hook_events: list[dict[str, Any]]
    generation_output: dict[str, Any]
    delegate_call_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def injection_count(self) -> int:
        return sum(1 for event in self.hook_events if event.get("injected") is True)

    @property
    def trigger_hit(self) -> bool:
        return any(event.get("trigger_hit") is True for event in self.hook_events)


class ForkRunner(Protocol):
    def run_arm(self, request: ForkArmRequest) -> ForkArmResult: ...
