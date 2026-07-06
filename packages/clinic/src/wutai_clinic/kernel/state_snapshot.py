"""Hash-verified agent-state snapshot / restore (factory-state testing).

Generalizes the cognition-ablation LUT workflow (manual copy + md5 check)
into a first-class mechanism: snapshot a declared set of state files, verify
integrity at any time, restore byte-faithfully, and prove the restore.

This is what enables "factory state vs adapted state" controlled comparisons
— run the same probe against a clean snapshot and the adapted live state,
then restore and PROVE the restore happened (the proof is the point: an
unverified restore silently contaminates every later experiment).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_MANIFEST = "snapshot_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take_snapshot(
    state_paths: list[Path],
    snapshot_dir: Path,
    *,
    label: str = "",
    taken_at: str | None = None,
) -> dict[str, Any]:
    """Copy each state file into ``snapshot_dir`` and record its hash."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, src in enumerate(state_paths):
        if not src.is_file():
            raise FileNotFoundError(f"state file missing: {src}")
        stored = snapshot_dir / f"{index:03d}__{src.name}"
        shutil.copy2(src, stored)
        entries.append(
            {
                "source_path": src.resolve().as_posix(),
                "stored_name": stored.name,
                "sha256": _sha256(src),
                "size_bytes": src.stat().st_size,
            }
        )
    manifest = {
        "label": label,
        "taken_at": taken_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": entries,
    }
    (snapshot_dir / SNAPSHOT_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def verify_snapshot(
    snapshot_dir: Path,
    *,
    against_live: bool = False,
) -> dict[str, Any]:
    """Check snapshot integrity; with ``against_live`` compare to live files.

    against_live=False — do the STORED copies still match the manifest?
    against_live=True  — do the LIVE source files currently match the
                         snapshot (i.e. is live state at snapshot state)?
    """
    manifest_path = snapshot_dir / SNAPSHOT_MANIFEST
    if not manifest_path.is_file():
        return {"ok": False, "error": "snapshot_manifest_missing"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatched: list[str] = []
    missing: list[str] = []
    for entry in manifest["entries"]:
        target = (
            Path(entry["source_path"])
            if against_live
            else snapshot_dir / entry["stored_name"]
        )
        if not target.is_file():
            missing.append(target.as_posix())
        elif _sha256(target) != entry["sha256"]:
            mismatched.append(target.as_posix())
    return {
        "ok": not mismatched and not missing,
        "compared": "live_sources" if against_live else "stored_copies",
        "entry_count": len(manifest["entries"]),
        "mismatched": mismatched,
        "missing": missing,
        "label": manifest.get("label", ""),
    }


def restore_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    """Copy stored files back over their sources, then prove the restore."""
    integrity = verify_snapshot(snapshot_dir, against_live=False)
    if not integrity["ok"]:
        raise RuntimeError(f"snapshot corrupted; refusing to restore: {integrity}")
    manifest = json.loads(
        (snapshot_dir / SNAPSHOT_MANIFEST).read_text(encoding="utf-8")
    )
    for entry in manifest["entries"]:
        shutil.copy2(snapshot_dir / entry["stored_name"], Path(entry["source_path"]))
    proof = verify_snapshot(snapshot_dir, against_live=True)
    if not proof["ok"]:
        raise RuntimeError(f"restore verification failed: {proof}")
    return {
        "restored_count": len(manifest["entries"]),
        "restore_verified": True,
        "label": manifest.get("label", ""),
    }


__all__ = ["SNAPSHOT_MANIFEST", "restore_snapshot", "take_snapshot", "verify_snapshot"]
