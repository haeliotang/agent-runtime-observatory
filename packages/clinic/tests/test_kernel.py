from __future__ import annotations

import json
from pathlib import Path

import pytest

from wutai_clinic.kernel import (
    ManipulationCheck,
    discordant_pair_test,
    evaluate_manipulation_checks,
    flip_rate_estimate,
    required_pairs,
    restore_snapshot,
    take_snapshot,
    verify_snapshot,
    wilson_interval,
)


# ---------------------------------------------------------------------------
# paired_stats re-exports stay wired to the canonical implementations
# ---------------------------------------------------------------------------


def test_paired_stats_reexports_work() -> None:
    test = discordant_pair_test(3, 1)
    assert 0.0 <= test["p_value"] <= 1.0
    lower, upper = wilson_interval(0, 6)
    assert lower == 0.0 and upper > 0.3
    assert flip_rate_estimate([False, False])["flip_count"] == 0
    assert (
        required_pairs(target_uplift_rate=0.3, trigger_hit_rate=1.0)["required_effective_pairs"] > 0
    )


# ---------------------------------------------------------------------------
# manipulation checks
# ---------------------------------------------------------------------------


def _arms() -> dict[str, list[dict]]:
    return {
        "treatment": [{"feature_active": True, "violations": 0} for _ in range(10)],
        "control": [{"feature_active": False, "violations": 0} for _ in range(10)],
    }


M_CHECKS = [
    ManipulationCheck(
        check_id="M1_treatment_active",
        description="feature active on every treatment row",
        arm="treatment",
        predicate=lambda row: row.get("feature_active") is True,
    ),
    ManipulationCheck(
        check_id="M1_control_separated",
        description="feature never active on control rows",
        arm="control",
        predicate=lambda row: row.get("feature_active") is True,
        kind="absent_in_arm",
    ),
    ManipulationCheck(
        check_id="M4_zero_violations",
        description="no guard violations anywhere",
        arm="treatment",
        predicate=lambda row: row.get("violations", 0) > 0,
        kind="absent_in_arm",
    ),
]


def test_manipulation_checks_all_pass() -> None:
    report = evaluate_manipulation_checks(M_CHECKS, _arms())
    assert report["all_passed"] is True
    assert all(r["passed"] for r in report["results"])


def test_manipulation_checks_detect_leak() -> None:
    arms = _arms()
    arms["control"][3]["feature_active"] = True  # arm separation broken
    report = evaluate_manipulation_checks(M_CHECKS, arms)
    assert report["all_passed"] is False
    failed = {r["check_id"] for r in report["results"] if not r["passed"]}
    assert failed == {"M1_control_separated"}


def test_manipulation_checks_min_rate() -> None:
    arms = _arms()
    arms["treatment"][0]["feature_active"] = False  # 9/10 active
    strict = evaluate_manipulation_checks(M_CHECKS, arms)
    assert strict["all_passed"] is False
    relaxed = evaluate_manipulation_checks(
        [
            ManipulationCheck(
                check_id="M1_soft",
                description="90% activation suffices",
                arm="treatment",
                predicate=lambda row: row.get("feature_active") is True,
                min_rate=0.9,
            )
        ],
        arms,
    )
    assert relaxed["all_passed"] is True


def test_manipulation_checks_empty_arm_fails() -> None:
    report = evaluate_manipulation_checks(M_CHECKS, {"treatment": [], "control": []})
    assert report["all_passed"] is False


# ---------------------------------------------------------------------------
# state snapshot
# ---------------------------------------------------------------------------


def _live_state(tmp_path: Path) -> list[Path]:
    live = tmp_path / "live"
    live.mkdir()
    lut = live / "taixuan-lut.json"
    lut.write_text(json.dumps({"entries": [1, 2, 3]}) + "\n", encoding="utf-8")
    recipe = live / "recipe_state.json"
    recipe.write_text(json.dumps({"version": 7}) + "\n", encoding="utf-8")
    return [lut, recipe]


def test_snapshot_verify_restore_roundtrip(tmp_path: Path) -> None:
    paths = _live_state(tmp_path)
    snap = tmp_path / "snap"
    manifest = take_snapshot(paths, snap, label="factory_state")
    assert len(manifest["entries"]) == 2
    assert verify_snapshot(snap)["ok"] is True
    assert verify_snapshot(snap, against_live=True)["ok"] is True

    # Adapt the live state, prove divergence, then restore and prove restore.
    paths[0].write_text(json.dumps({"entries": [1, 2, 3, 4, 5]}) + "\n", encoding="utf-8")
    diverged = verify_snapshot(snap, against_live=True)
    assert diverged["ok"] is False
    assert len(diverged["mismatched"]) == 1

    result = restore_snapshot(snap)
    assert result["restore_verified"] is True
    assert verify_snapshot(snap, against_live=True)["ok"] is True
    assert json.loads(paths[0].read_text())["entries"] == [1, 2, 3]


def test_snapshot_refuses_restore_when_corrupted(tmp_path: Path) -> None:
    paths = _live_state(tmp_path)
    snap = tmp_path / "snap"
    take_snapshot(paths, snap)
    # Corrupt a stored copy.
    stored = next(snap.glob("000__*"))
    stored.write_text("corrupted\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to restore"):
        restore_snapshot(snap)


def test_snapshot_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        take_snapshot([tmp_path / "ghost.json"], tmp_path / "snap")


def test_verify_missing_manifest(tmp_path: Path) -> None:
    assert verify_snapshot(tmp_path)["ok"] is False
