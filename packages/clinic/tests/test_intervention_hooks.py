from __future__ import annotations

from pathlib import Path

from wutai_clinic.intervention.hooks import (
    LiveFeatureHook,
    StaticPrefixHook,
    build_phase315_dry_run_events,
    build_phase316_dry_run_events,
)
from wutai_clinic.io import read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_static_prefix_hook_preserves_simple_maybe_inject_api() -> None:
    hook = StaticPrefixHook(trigger_index=3, policy_message="stop and validate")
    messages = [{"role": "user", "content": "task"}]

    assert hook.maybe_inject(2, messages) is messages
    injected = hook.maybe_inject(3, messages)

    assert injected == [
        {"role": "user", "content": "task"},
        {"role": "system", "content": "stop and validate"},
    ]
    assert hook.maybe_inject(4, injected) is injected


def test_live_feature_hook_preserves_simple_maybe_inject_api_and_debounce() -> None:
    hook = LiveFeatureHook(
        feature_schema={"features": ["policy_id"]},
        trigger_condition={"policy_id": "same_action_escape"},
        policy_message="switch evidence source",
    )
    messages = [{"role": "user", "content": "task"}]

    assert hook.maybe_inject({"policy_id": "insert_validation_checkpoint"}, messages) is messages
    injected = hook.maybe_inject({"policy_id": "same_action_escape"}, messages)

    assert injected == [
        {"role": "user", "content": "task"},
        {"role": "system", "content": "switch evidence source"},
    ]
    assert hook.maybe_inject({"policy_id": "same_action_escape"}, injected) is injected


def test_phase315_static_prefix_dry_run_matches_frozen_hook_events() -> None:
    bridge_plan = list(read_jsonl(MODELS / "phase315_paired_runner_policy_bridge_plan.jsonl"))
    expected_events = list(
        read_jsonl(MODELS / "phase315_exact_paired_runner_hook_dry_run_events.jsonl")
    )

    assert build_phase315_dry_run_events(bridge_plan) == expected_events


def test_phase316_live_feature_dry_run_matches_frozen_hook_events() -> None:
    candidate_rows = list(
        read_jsonl(MODELS / "phase316_live_trigger_recalibration_batch3_candidates.jsonl")
    )
    expected_events = list(read_jsonl(MODELS / "phase316_live_feature_hook_dry_run_events.jsonl"))

    assert build_phase316_dry_run_events(candidate_rows) == expected_events
