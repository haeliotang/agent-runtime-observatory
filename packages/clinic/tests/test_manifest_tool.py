from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wutai_clinic.cli import app
from wutai_clinic.evidence.manifest_tool import (
    MANIFEST_NAME,
    generate_manifest_file,
    verify_manifest_file,
)

runner = CliRunner()


def _tree(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.json").write_text('{"x": 1}\n', encoding="utf-8")
    (root / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")


def test_generate_and_verify_roundtrip(tmp_path: Path) -> None:
    _tree(tmp_path)
    result = generate_manifest_file(tmp_path, note="unit test")
    assert result["file_count"] == 2
    assert (tmp_path / MANIFEST_NAME).is_file()
    check = verify_manifest_file(tmp_path)
    assert check["ok"] is True
    assert check["mismatched"] == []
    assert check["missing"] == []
    assert check["untracked"] == []


def test_verify_detects_mismatch(tmp_path: Path) -> None:
    _tree(tmp_path)
    generate_manifest_file(tmp_path)
    (tmp_path / "a.json").write_text('{"x": 2}\n', encoding="utf-8")
    check = verify_manifest_file(tmp_path)
    assert check["ok"] is False
    assert check["mismatched"] == ["a.json"]


def test_verify_detects_missing_and_untracked(tmp_path: Path) -> None:
    _tree(tmp_path)
    generate_manifest_file(tmp_path)
    (tmp_path / "sub" / "b.bin").unlink()
    (tmp_path / "new.txt").write_text("late arrival\n", encoding="utf-8")
    check = verify_manifest_file(tmp_path)
    assert check["ok"] is False
    assert check["missing"] == ["sub/b.bin"]
    assert check["untracked"] == ["new.txt"]


def test_verify_without_manifest(tmp_path: Path) -> None:
    _tree(tmp_path)
    check = verify_manifest_file(tmp_path)
    assert check["ok"] is False
    assert check["error"] == "manifest_missing"


def test_manifest_excluded_from_itself(tmp_path: Path) -> None:
    _tree(tmp_path)
    generate_manifest_file(tmp_path)
    # Regenerating after a verify must stay stable (manifest not self-hashed).
    generate_manifest_file(tmp_path, generated_at="2026-06-13T00:00:00Z")
    first = (tmp_path / MANIFEST_NAME).read_text()
    generate_manifest_file(tmp_path, generated_at="2026-06-13T00:00:00Z")
    assert (tmp_path / MANIFEST_NAME).read_text() == first


def test_cli_generate_then_verify(tmp_path: Path) -> None:
    _tree(tmp_path)
    gen = runner.invoke(app, ["evidence-manifest", str(tmp_path), "--note", "cli test"])
    assert gen.exit_code == 0, gen.output
    payload = json.loads(gen.output)
    assert payload["file_count"] == 2
    check = runner.invoke(app, ["evidence-manifest", str(tmp_path), "--verify"])
    assert check.exit_code == 0, check.output
    assert json.loads(check.output)["ok"] is True


def test_cli_verify_failure_exit_code(tmp_path: Path) -> None:
    _tree(tmp_path)
    runner.invoke(app, ["evidence-manifest", str(tmp_path)])
    (tmp_path / "a.json").write_text("tampered\n", encoding="utf-8")
    check = runner.invoke(app, ["evidence-manifest", str(tmp_path), "--verify"])
    assert check.exit_code == 1
