from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from wutai_clinic.engine import constants
from wutai_clinic.engine.str_calculator import (
    calculate_rolling_str,
    calculate_window_str,
    detect_str_anomaly,
    get_semantic_similarity,
    get_state_signature,
)
from wutai_clinic.schemas import Trajectory

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
MODELS = OBSERVATORY_ROOT / "models"
LEGACY_ANALYTICS = (
    OBSERVATORY_ROOT / "software-agent-sdk-main/scripts/trajectory_efficiency_analytics.py"
)


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _first_trajectory() -> dict:
    with (MODELS / "trajectories_purified.jsonl").open(encoding="utf-8") as handle:
        return json.loads(next(handle))


def test_constants_match_legacy_analytics() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    assert constants.BFT_F == legacy.BFT_F
    assert constants.TOPOLOGICAL_IMPEDANCE == legacy.TOPOLOGICAL_IMPEDANCE
    assert constants.BOOTSTRAP_MIN_SAMPLES == legacy.BOOTSTRAP_MIN_SAMPLES


def test_state_signature_matches_legacy_analytics() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    turns = _first_trajectory()["sft_turns"]
    for turn in turns:
        assert get_state_signature(turn) == legacy.get_state_signature(turn)


def test_structural_window_str_matches_legacy_analytics() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    turns = _first_trajectory()["sft_turns"]
    for window_size in range(2, min(6, len(turns) + 1)):
        window = turns[:window_size]
        legacy_states = [legacy.get_state_signature(turn) for turn in window]
        expected = legacy.calculate_window_str(legacy_states)
        assert calculate_window_str(window, mode="structural") == pytest.approx(expected, abs=1e-12)


def test_semantic_window_str_matches_legacy_analytics() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    turns = _first_trajectory()["sft_turns"][:5]
    assert get_semantic_similarity(turns[0], turns[1]) == pytest.approx(
        legacy.get_semantic_similarity(turns[0], turns[1]), abs=1e-12
    )
    assert calculate_window_str(turns, mode="semantic") == pytest.approx(
        legacy.calculate_window_str_semantic(turns), abs=1e-12
    )


def test_rolling_str_matches_legacy_analytics() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    row = _first_trajectory()
    trajectory = Trajectory.from_dict(row)
    expected = []
    turns = row["sft_turns"]
    for index in range(len(turns)):
        window = turns[max(0, index - 4) : index + 1]
        states = [legacy.get_state_signature(turn) for turn in window]
        expected.append(legacy.calculate_window_str(states))
    assert calculate_rolling_str(trajectory, window_size=5) == pytest.approx(expected, abs=1e-12)


def test_detect_str_anomaly_supports_distribution_methods() -> None:
    rolling = [0.0, 0.0, 0.0, 1.0]

    assert detect_str_anomaly(rolling) == [3]
    assert detect_str_anomaly(rolling, method="zscore") == [3]
    assert detect_str_anomaly(rolling, threshold=10.0) == []


def test_detect_str_anomaly_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="method must be"):
        detect_str_anomaly([0.0, 0.0, 1.0], method="unknown")
