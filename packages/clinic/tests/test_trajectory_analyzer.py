from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from wutai_clinic.engine.trajectory_analyzer import analyze, analyze_corpus
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


def _first_rows(count: int) -> list[dict]:
    rows = []
    with (MODELS / "trajectories_purified.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
            if len(rows) == count:
                break
    return rows


def test_single_trajectory_metrics_match_legacy_analytics_first_20() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    for row in _first_rows(20):
        expected = legacy.analyze_single_trajectory(row["sft_turns"])
        actual = analyze(Trajectory.from_dict(row)).to_dict()
        assert actual.keys() == expected.keys()
        for key, expected_value in expected.items():
            assert actual[key] == pytest.approx(expected_value, abs=1e-12)


def test_corpus_report_matches_legacy_analytics_shape_and_values_first_20() -> None:
    legacy = _load_module(LEGACY_ANALYTICS)
    rows = _first_rows(20)
    legacy_rows = [legacy.analyze_single_trajectory(row["sft_turns"]) for row in rows]
    report = analyze_corpus([Trajectory.from_dict(row) for row in rows])

    def mean(key: str) -> float:
        return sum(float(row[key]) for row in legacy_rows) / len(legacy_rows)

    def pstdev(key: str) -> float:
        values = [float(row[key]) for row in legacy_rows]
        avg = sum(values) / len(values)
        return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5

    metrics = report["metrics"]
    assert report["total_trajectories"] == 20
    assert metrics["optimal_path_length"]["mean"] == pytest.approx(mean("steps"), abs=1e-12)
    assert metrics["optimal_path_length"]["std"] == pytest.approx(pstdev("steps"), abs=1e-12)
    assert metrics["epistemic_pragmatic_ratio"]["mean"] == pytest.approx(
        mean("epistemic_ratio"), abs=1e-12
    )
    assert metrics["tool_shannon_entropy"]["mean"] == pytest.approx(mean("tool_entropy"), abs=1e-12)
    assert metrics["exploration_exploitation_gradient"]["mean"] == pytest.approx(
        mean("convergence_gradient"), abs=1e-12
    )
    assert metrics["total_rollbacks"] == int(sum(row["rollbacks"] for row in legacy_rows))
    assert metrics["error_recovery_latency"]["mean"] == pytest.approx(
        mean("error_recovery_latency"), abs=1e-12
    )
    assert metrics["hypothesis_shift_rate"]["mean"] == pytest.approx(
        mean("hypothesis_shift_rate"), abs=1e-12
    )
    assert metrics["soft_topological_return"]["mean"] == pytest.approx(mean("avg_str"), abs=1e-12)
    assert metrics["estimated_trits_consumption"]["mean"] == pytest.approx(
        mean("epistemic_token_efficiency"), abs=1e-12
    )
