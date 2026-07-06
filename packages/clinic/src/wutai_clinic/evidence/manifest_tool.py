"""Evidence-root hash manifests: generate and verify.

Replaces the hand-maintained ``MANIFEST.sha256`` workflow (cognition ablation)
with a clinic command. The manifest is the git-resident integrity record for
evidence trees whose binary payloads live outside git (storage policy:
``tasks/wave4/evidence_storage_policy.md``).

Format (one line per file, sorted by path):
    <relative/path>  <sha256>  <size_bytes>
Lines starting with ``#`` are header comments and are ignored by verify.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "MANIFEST.sha256"

# Directories never worth hashing (transient harness state).
_SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_evidence_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def generate_manifest_file(
    root: Path,
    *,
    note: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write ``MANIFEST.sha256`` covering every file under ``root``."""
    if not root.is_dir():
        raise FileNotFoundError(f"evidence root not found: {root}")
    rows = []
    total_bytes = 0
    for path in iter_evidence_files(root):
        size = path.stat().st_size
        total_bytes += size
        rows.append(f"{path.relative_to(root).as_posix()}  {_sha256(path)}  {size}")
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = [
        f"# {MANIFEST_NAME}",
        f"# Generated: {stamp} by wutai-clinic evidence-manifest",
        f"# Files: {len(rows)}  Total bytes: {total_bytes}",
    ]
    if note:
        header.append(f"# Note: {note}")
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text("\n".join(header + [""] + rows) + "\n", encoding="utf-8")
    return {
        "manifest_path": manifest_path,
        "file_count": len(rows),
        "total_bytes": total_bytes,
    }


def verify_manifest_file(root: Path) -> dict[str, Any]:
    """Recompute hashes against ``MANIFEST.sha256``; report every discrepancy."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return {
            "ok": False,
            "error": "manifest_missing",
            "manifest_path": manifest_path,
        }
    expected: dict[str, tuple[str, int]] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit("  ", 2)
        if len(parts) != 3:
            continue
        rel, digest, size = parts
        expected[rel] = (digest, int(size))

    mismatched: list[str] = []
    missing: list[str] = []
    for rel, (digest, _size) in expected.items():
        path = root / rel
        if not path.is_file():
            missing.append(rel)
        elif _sha256(path) != digest:
            mismatched.append(rel)
    actual = {p.relative_to(root).as_posix() for p in iter_evidence_files(root)}
    untracked = sorted(actual - set(expected))
    ok = not mismatched and not missing and not untracked
    return {
        "ok": ok,
        "manifest_path": manifest_path,
        "expected_count": len(expected),
        "mismatched": sorted(mismatched),
        "missing": sorted(missing),
        "untracked": untracked,
    }


__all__ = [
    "MANIFEST_NAME",
    "generate_manifest_file",
    "iter_evidence_files",
    "verify_manifest_file",
]
