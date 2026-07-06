from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from wutai_clinic.intervention.runner import (
    build_batch_plan,
    build_smoke_plan,
    filter_pending_arms,
    provider_summary,
    runner_args_for_arm,
    scrub_raw_generated_artifacts,
    select_batch_rows,
    select_pair_rows,
    selected_bridge_rows_for_batch,
    with_per_arm_call_limit,
)
from wutai_clinic.io import read_jsonl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent.parent
MODELS = PACKAGE_ROOT.parent / "models"
PROVIDER_CONFIG = (
    REPO_ROOT / "Wutai_observatory/software-agent-sdk-main/scripts/harvest_gpt5_5_engineering.py"
)
SWE_AGENT_CONFIG = (
    REPO_ROOT / "Wutai_observatory/software-agent-sdk-main/swe_agent_src/config/default.yaml"
)
LEGACY_SMOKE = MODELS / "run_phase315_one_pair_smoke.py"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def _without_runner_hash(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in row.items() if key != "runner_arg_sha256"} for row in rows
    ]


def test_phase315_one_pair_smoke_plan_matches_frozen_plan() -> None:
    bridge_plan = list(read_jsonl(MODELS / "phase315_paired_runner_policy_bridge_plan.jsonl"))
    expected_plan = list(read_jsonl(MODELS / "phase315_one_pair_smoke_plan.jsonl"))
    provider = provider_summary(PROVIDER_CONFIG, acknowledge_external_export=True)

    selected_rows = select_pair_rows(bridge_plan)
    smoke_plan = build_smoke_plan(
        selected_rows=selected_rows,
        smoke_root=MODELS / "phase315_one_pair_smoke_runs",
        provider=provider,
        swe_agent_config=SWE_AGENT_CONFIG,
        runner_wrapper=LEGACY_SMOKE,
    )

    assert _without_runner_hash(smoke_plan) == _without_runner_hash(expected_plan)
    legacy = _load_module(LEGACY_SMOKE)
    for actual, source_row in zip(smoke_plan, selected_rows, strict=True):
        expected_hash = legacy.stable_json_hash(
            runner_args_for_arm(
                row=source_row,
                run_root=MODELS / "phase315_one_pair_smoke_runs",
                swe_agent_config=SWE_AGENT_CONFIG,
                provider_model=provider["declared_model"],
            )
        )
        assert actual["runner_arg_sha256"] == expected_hash


def test_phase316_batch_plan_matches_frozen_uncapped_batch01_plan() -> None:
    schedule = list(read_jsonl(MODELS / "phase316_paired_uplift_execution_schedule.jsonl"))
    bridge_plan = list(read_jsonl(MODELS / "phase315_paired_runner_policy_bridge_plan.jsonl"))
    expected_plan = list(read_jsonl(MODELS / "phase316_batch01_uncapped_execution_plan.jsonl"))
    provider = provider_summary(PROVIDER_CONFIG, acknowledge_external_export=True)
    selected_rows = with_per_arm_call_limit(select_batch_rows(schedule, 1), 0)
    selected_bridge_rows = selected_bridge_rows_for_batch(
        bridge_plan=bridge_plan,
        selected_rows=selected_rows,
    )

    batch_plan = build_batch_plan(
        selected_rows=selected_rows,
        selected_bridge_rows=selected_bridge_rows,
        provider=provider,
        swe_agent_config=SWE_AGENT_CONFIG,
        run_root=MODELS / "phase316_paired_uplift_runs_uncapped",
        batch_index=1,
        per_arm_model_call_limit=0,
    )

    assert batch_plan == expected_plan


def test_filter_pending_arms_skips_completed_resumable_state() -> None:
    rows = [
        {"arm_id": "a", "resumable_state_key_sha256": "done"},
        {"arm_id": "b", "resumable_state_key_sha256": "pending"},
    ]

    assert filter_pending_arms(rows, {"done"}) == [rows[1]]


def test_scrub_raw_generated_artifacts_hashes_then_removes(tmp_path: Path) -> None:
    raw_files = [
        tmp_path / "run.traj",
        tmp_path / "run.log",
        tmp_path / "run_batch.config.yaml",
        tmp_path / "model_input_debug.log",
    ]
    safe_file = tmp_path / "safe.json"
    for path in raw_files:
        path.write_text(f"raw artifact {path.name}", encoding="utf-8")
    safe_file.write_text("safe", encoding="utf-8")

    audit_rows = scrub_raw_generated_artifacts(tmp_path)

    assert len(audit_rows) == len(raw_files)
    assert all(row["privacy_action"] == "sha256_audited_then_removed" for row in audit_rows)
    assert all(not path.exists() for path in raw_files)
    assert safe_file.exists()
