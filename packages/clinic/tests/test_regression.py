from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from wutai_clinic.engine.pruner import apply_source_policy
from wutai_clinic.engine.str_calculator import calculate_window_str, get_state_signature
from wutai_clinic.engine.trajectory_analyzer import analyze, analyze_corpus
from wutai_clinic.schemas import Trajectory

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
MODELS = OBSERVATORY_ROOT / "models"
LEGACY_ANALYTICS = (
    OBSERVATORY_ROOT / "software-agent-sdk-main/scripts/trajectory_efficiency_analytics.py"
)
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


def test_regression_str_calculator_vs_legacy_first_row() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    row = _first_rows(1)[0]
    turns = row["sft_turns"][:5]
    expected_states = [legacy.get_state_signature(turn) for turn in turns]
    actual_states = [get_state_signature(turn) for turn in turns]

    assert actual_states == expected_states
    assert calculate_window_str(turns, mode="structural") == pytest.approx(
        legacy.calculate_window_str(expected_states),
        abs=1e-12,
    )


def test_regression_pruner_vs_legacy_first_rows() -> None:
    legacy = _load_module(LEGACY_PRUNER)
    for row in _first_rows(5):
        source_kind = row.get("_wutai_source_kind", "general")
        assert apply_source_policy(row["sft_turns"], source_kind) == legacy.apply_source_policy(
            row["sft_turns"],
            source_kind,
        )


def test_regression_analyzer_corpus_vs_legacy_first_20() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    rows = _first_rows(20)
    expected_rows = [legacy.analyze_single_trajectory(row["sft_turns"]) for row in rows]
    actual_rows = [analyze(Trajectory.from_dict(row)).to_dict() for row in rows]
    for actual, expected in zip(actual_rows, expected_rows, strict=True):
        for key, expected_value in expected.items():
            assert actual[key] == pytest.approx(expected_value, abs=1e-12)

    report = analyze_corpus([Trajectory.from_dict(row) for row in rows])
    assert report["total_trajectories"] == 20


def test_analyze_corpus_accepts_jsonl_path(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    sample.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in _first_rows(3)))

    report = analyze_corpus(sample)

    assert report["total_trajectories"] == 3


def test_regression_analyzer_full_corpus_matches_legacy_report() -> None:
    expected = json.loads((MODELS / "efe_dynamics_report.json").read_text())
    actual = analyze_corpus(MODELS / "trajectories_purified.jsonl")

    assert actual["total_trajectories"] == expected["total_trajectories"]
    for metric_name, expected_value in expected["metrics"].items():
        actual_value = actual["metrics"][metric_name]
        if isinstance(expected_value, dict):
            for key, value in expected_value.items():
                if isinstance(value, str):
                    assert actual_value[key] == value
                else:
                    assert actual_value[key] == pytest.approx(value, abs=1e-10)
        else:
            assert actual_value == pytest.approx(expected_value, abs=1e-10)
