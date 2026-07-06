from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from wutai_clinic.engine.pruner import (
    apply_source_policy,
    compute_quality_score,
    prune_corpus,
    prune_single_trajectory,
    prune_trajectory,
    pruning_policy_for_source,
)
from wutai_clinic.io import read_jsonl
from wutai_clinic.schemas import Trajectory

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
MODELS = OBSERVATORY_ROOT / "models"
LEGACY_PRUNER = OBSERVATORY_ROOT / "software-agent-sdk-main/scripts/str_pruner.py"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _first_rows(count: int) -> list[dict]:
    rows = []
    with (MODELS / "trajectories_purified.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
            if len(rows) == count:
                break
    return rows


def test_pruning_policy_matches_legacy() -> None:
    legacy = _load_module(LEGACY_PRUNER)
    for source_kind in [
        "swe_long",
        "gui_temporal",
        "physical_temporal",
        "mbpp_base",
        "audio_short",
        "general",
    ]:
        assert pruning_policy_for_source(source_kind) == legacy.pruning_policy_for_source(
            source_kind
        )


def test_quality_score_matches_legacy_first_20() -> None:
    legacy = _load_module(LEGACY_PRUNER)
    for row in _first_rows(20):
        source_kind = row.get("_wutai_source_kind", "general")
        expected = legacy.compute_quality_score(source_kind, row["sft_turns"])
        assert compute_quality_score(source_kind, row["sft_turns"]) == pytest.approx(
            expected, abs=1e-12
        )


def test_prune_single_trajectory_matches_legacy_on_repeated_window() -> None:
    legacy = _load_module(LEGACY_PRUNER)
    repeated_turn = {
        "role": "assistant",
        "content": "repeat",
        "tool_call": {"name": "run_command", "arguments": {"command": "pwd"}},
    }
    turns = [
        {"role": "user", "content": "task"},
        repeated_turn,
        copy.deepcopy(repeated_turn),
        copy.deepcopy(repeated_turn),
        copy.deepcopy(repeated_turn),
        copy.deepcopy(repeated_turn),
        {"role": "tool", "content": "done"},
    ]
    expected = legacy.prune_single_trajectory(copy.deepcopy(turns), semantic_kernel=False)
    actual = prune_single_trajectory(copy.deepcopy(turns), semantic_kernel=False)
    assert actual == expected


def test_apply_source_policy_matches_legacy_first_long_rows() -> None:
    legacy = _load_module(LEGACY_PRUNER)
    for row in _first_rows(10):
        source_kind = row.get("_wutai_source_kind", "general")
        expected = legacy.apply_source_policy(copy.deepcopy(row["sft_turns"]), source_kind)
        actual = apply_source_policy(copy.deepcopy(row["sft_turns"]), source_kind)
        assert actual == expected


def test_prune_trajectory_updates_policy_and_quality() -> None:
    row = _first_rows(1)[0]
    pruned = prune_trajectory(Trajectory.from_dict(row)).to_dict()
    assert pruned["_wutai_pruning_policy"] == pruning_policy_for_source(row["_wutai_source_kind"])
    assert pruned["_wutai_quality_score"] == pytest.approx(
        compute_quality_score(row["_wutai_source_kind"], pruned["sft_turns"]), abs=1e-12
    )


def test_prune_corpus_target_hygiene_matches_legacy_manifest_count() -> None:
    rows = list(read_jsonl(MODELS / "trajectories_purified.jsonl"))
    legacy_manifest = json.loads((MODELS / "trajectories_hygiene_manifest.json").read_text())

    trajectories, stats, hygiene_result = prune_corpus(rows, dedup=False, target_hygiene=True)

    assert hygiene_result is not None
    assert stats.input_count == legacy_manifest["total_raw"]
    assert stats.hygiene_filtered == legacy_manifest["total_filtered"]
    assert stats.output_count == legacy_manifest["total_purified"]
    assert len(trajectories) == legacy_manifest["total_purified"]
    assert hygiene_result.manifest["promotion_gate"] == legacy_manifest["promotion_gate"]
