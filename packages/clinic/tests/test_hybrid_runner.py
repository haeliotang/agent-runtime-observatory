from __future__ import annotations

from typing import Any

from wutai_clinic.intervention.hybrid_runner import (
    CapsuleBuildContext,
    CapsuleMaterializationHook,
    HybridReplayGenerationModel,
    message_prefix_hash,
    normalize_live_message_prefix,
)
from wutai_clinic.intervention.replay_protocol import InterventionProtocol, StateCapsule


class DummyStats:
    def model_dump(self) -> dict[str, int]:
        return {"api_calls": 1}


class DelegateModel:
    def __init__(self):
        self.calls: list[list[dict[str, Any]]] = []
        self.stats = DummyStats()

    def query(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append([dict(message) for message in history])
        return {"message": "generated action"}


class Agent:
    def __init__(self, model: HybridReplayGenerationModel):
        self.model = model


def _protocol() -> InterventionProtocol:
    return InterventionProtocol.from_dict(
        {
            "trigger": {"type": "live_feature", "predicate": "error_streak >= 3"},
            "action": {
                "type": "inject_system_prompt",
                "message_id": "break_recurrence_and_replan",
            },
            "guard": {"debounce": "once_per_pair", "raw_payload_logging": False},
            "claim": {"allowed": "bounded_next_step_control"},
        }
    )


def _capsule_from_messages(
    messages: list[dict[str, Any]],
    **overrides: str,
) -> StateCapsule:
    data = {
        "task_id": "sympy__sympy-21627",
        "repo_hash": "repo-sha",
        "agent_config_hash": "agent-sha",
        "provider_config_hash": "provider-sha",
        "message_prefix_hash": message_prefix_hash(messages),
        "working_tree_diff_hash": "diff-sha",
        "observation_window_hash": "obs-sha",
        "model_request_hash": "model-sha",
        "runner_config_hash": "runner-sha",
        "deployment_hash": "deploy-sha",
        "replay_config_hash": "replay-sha",
        "runtime_nondeterminism_policy": "single_worker_temperature_zero",
    }
    data.update(overrides)
    return StateCapsule.from_dict(data)


def _capsule_builder(context: CapsuleBuildContext) -> StateCapsule:
    return _capsule_from_messages(context.messages)


def test_hybrid_model_replays_prefix_then_delegates_generation() -> None:
    delegate = DelegateModel()
    model = HybridReplayGenerationModel(
        replay_actions=[{"message": "historical action"}, "second historical action"],
        delegate=delegate,
    )

    assert model.next_phase == "replay"
    assert model.query([]) == {"message": "historical action"}
    assert model.query([]) == {"message": "second historical action"}
    assert model.next_phase == "generation"
    assert model.query([{"role": "user", "content": "live"}]) == {"message": "generated action"}

    assert len(delegate.calls) == 1
    assert [event["phase"] for event in model.event_rows()] == [
        "replay",
        "replay",
        "generation",
    ]
    assert [event["delegated"] for event in model.event_rows()] == [False, False, True]
    assert model.stats.model_dump() == {"api_calls": 1}


def test_message_prefix_hash_ignores_transport_ids_but_keeps_semantics() -> None:
    messages = [
        {"role": "user", "content": "task", "cache_control": {"type": "ephemeral"}},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_random_a",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command":"pytest tests/defer"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "OBSERVATION:\nOK",
            "tool_call_ids": ["call_random_a"],
        },
    ]
    same_semantics = [
        {"role": "user", "content": "task", "cache_control": {"type": "other"}},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_random_b",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command":"pytest tests/defer"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "OBSERVATION:\nOK",
            "tool_call_ids": ["call_random_b"],
        },
    ]
    different_command = [
        messages[0],
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_random_a",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command":"pytest tests/queries"}',
                    },
                }
            ],
        },
        messages[2],
    ]
    different_observation = [messages[0], messages[1], {"role": "tool", "content": "ERR"}]

    assert message_prefix_hash(messages) == message_prefix_hash(same_semantics)
    assert message_prefix_hash(messages) != message_prefix_hash(different_command)
    assert message_prefix_hash(messages) != message_prefix_hash(different_observation)


def test_live_message_prefix_normalization_preserves_tool_call_linkage() -> None:
    messages = [
        {
            "role": "tool",
            "content": "OBSERVATION:\nRan 29 tests in 0.054s\nOK",
            "tool_call_ids": ["call_random_a"],
        }
    ]

    normalize_live_message_prefix(messages)

    assert messages[0]["tool_call_ids"] == ["call_random_a"]
    assert messages[0]["content"] == "OBSERVATION:\nRan 29 tests in <DURATION>s\nOK"


def test_capsule_hook_materializes_only_at_first_generation_query_and_injects_once() -> None:
    delegate = DelegateModel()
    model = HybridReplayGenerationModel(
        replay_actions=[{"message": "historical action"}],
        delegate=delegate,
    )
    agent = Agent(model)
    protocol = _protocol()
    generation_messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "historical action"},
        {"role": "tool", "content": "historical observation"},
    ]
    hook = CapsuleMaterializationHook(
        arm_type="treatment",
        protocol=protocol,
        capsule_builder=_capsule_builder,
        feature_extractor=lambda _context: {"error_streak": 3},
        reference_capsule=_capsule_from_messages(generation_messages),
    )
    hook.on_init(agent=agent)

    replay_messages = [{"role": "user", "content": "task"}]
    hook.on_model_query(messages=replay_messages, agent="main")
    assert hook.safe_audit_events == []
    assert model.query(replay_messages) == {"message": "historical action"}

    hook.on_model_query(messages=generation_messages, agent="main")
    assert model.query(generation_messages) == {"message": "generated action"}
    hook.on_model_query(messages=generation_messages, agent="main")

    assert hook.injection_count == 1
    assert len(hook.safe_audit_events) == 1
    event = hook.safe_audit_events[0]
    assert event["fork_passed"] is True
    assert event["trigger_hit"] is True
    assert event["injected"] is True
    assert event["message_delta"] == 1
    assert event["pre_intervention_message_prefix_hash"] != event["post_intervention_request_hash"]
    assert event["raw_payload_logged"] is False
    assert delegate.calls[0][-1]["role"] == "system"


def test_capsule_hook_blocks_treatment_injection_on_reference_mismatch() -> None:
    delegate = DelegateModel()
    model = HybridReplayGenerationModel(replay_actions=[], delegate=delegate)
    messages = [{"role": "user", "content": "task"}]
    hook = CapsuleMaterializationHook(
        arm_type="treatment",
        protocol=_protocol(),
        capsule_builder=_capsule_builder,
        feature_extractor=lambda _context: {"error_streak": 3},
        reference_capsule=_capsule_from_messages(messages, model_request_hash="different"),
    )
    hook.on_init(agent=Agent(model))

    hook.on_model_query(messages=messages, agent="main")

    assert hook.injection_count == 0
    assert len(messages) == 1
    assert hook.safe_audit_events[0]["fork_decision"] == "state_mismatch_no_attribution"
    assert hook.safe_audit_events[0]["fork_passed"] is False
    assert hook.safe_audit_events[0]["injected"] is False


def test_control_arm_materializes_capsule_without_injection() -> None:
    delegate = DelegateModel()
    model = HybridReplayGenerationModel(replay_actions=[], delegate=delegate)
    messages = [{"role": "user", "content": "task"}]
    hook = CapsuleMaterializationHook(
        arm_type="control",
        protocol=_protocol(),
        capsule_builder=_capsule_builder,
        feature_extractor=lambda _context: {"error_streak": 3},
    )
    hook.on_init(agent=Agent(model))

    hook.on_model_query(messages=messages, agent="main")

    assert hook.capsule is not None
    assert hook.injection_count == 0
    assert hook.safe_audit_events[0]["trigger_hit"] is True
    assert hook.safe_audit_events[0]["injected"] is False
    assert hook.safe_audit_events[0]["message_delta"] == 0
