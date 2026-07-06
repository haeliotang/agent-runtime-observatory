from __future__ import annotations

from pathlib import Path

import pytest

from wutai_clinic.io import count_jsonl, read_jsonl, write_jsonl
from wutai_clinic.schemas import Trajectory

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MODELS = PACKAGE_ROOT.parent / "models"


def test_jsonl_count_and_schema_read() -> None:
    path = MODELS / "trajectories_purified.jsonl"
    assert count_jsonl(path) > 100
    first = next(read_jsonl(path, Trajectory))
    assert isinstance(first, Trajectory)


def test_jsonl_write_round_trip(tmp_path: Path, first_trajectories: list[dict]) -> None:
    output = tmp_path / "roundtrip.jsonl"
    write_jsonl(output, first_trajectories)
    assert count_jsonl(output) == len(first_trajectories)
    assert list(read_jsonl(output)) == first_trajectories


def test_jsonl_schema_round_trip_first_100(tmp_path: Path) -> None:
    path = MODELS / "trajectories_purified.jsonl"
    rows = []
    for index, trajectory in enumerate(read_jsonl(path, Trajectory), start=1):
        rows.append(trajectory)
        if index == 100:
            break
    output = tmp_path / "schema-roundtrip.jsonl"
    write_jsonl(output, rows)
    assert list(read_jsonl(output)) == [row.to_dict() for row in rows]


def test_empty_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert count_jsonl(path) == 0
    assert list(read_jsonl(path)) == []


def test_bad_jsonl_can_skip(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
    assert list(read_jsonl(path, skip_bad_lines=True)) == [{"ok": True}]
    with pytest.raises(ValueError):
        list(read_jsonl(path))
