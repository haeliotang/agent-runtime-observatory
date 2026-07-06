from __future__ import annotations

import json
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
MODELS = OBSERVATORY_ROOT / "models"

# Legacy-artifact roots: present in the private research monorepo, absent in a
# standalone checkout. Regression tests that replay those artifacts skip
# (with an explicit reason) instead of failing when the roots are missing.
# Set WUTAI_OBS_ARTIFACTS=1 to force failures instead (monorepo CI).
_ARTIFACT_MARKERS = (
    "/models/",
    "/software-agent-sdk-main/",
    "/Wutai_observatory/",
)


MONOREPO_PRESENT = MODELS.is_dir()
requires_monorepo = pytest.mark.skipif(
    not MONOREPO_PRESENT,
    reason="requires private monorepo artifacts/stack (models/, SWE-agent install)",
)

try:
    import importlib.util

    HAS_SWEREX = importlib.util.find_spec("swerex") is not None
except Exception:  # pragma: no cover
    HAS_SWEREX = False
requires_swerex = pytest.mark.skipif(
    not HAS_SWEREX, reason="requires swerex (optional SWE-agent runtime dependency)"
)


def _is_missing_artifact(exc: BaseException) -> str | None:
    if type(exc).__name__ == "MissingArtifact":
        return str(exc)
    if not isinstance(exc, FileNotFoundError):
        return None
    name = str(getattr(exc, "filename", "") or "")
    if any(marker in name or name.endswith(marker.strip("/")) for marker in _ARTIFACT_MARKERS):
        return name
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    import os

    outcome = yield
    if os.environ.get("WUTAI_OBS_ARTIFACTS") == "1":
        return
    exc = outcome.excinfo
    if exc is not None:
        missing = _is_missing_artifact(exc[1])
        if missing is not None:
            pytest.skip(f"legacy monorepo artifact not present: {missing}")


@pytest.fixture
def first_trajectories() -> list[dict]:
    path = MODELS / "trajectories_purified.jsonl"
    if not path.is_file():
        pytest.skip(f"legacy monorepo artifact not present: {path}")
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
            if len(rows) == 5:
                break
    return rows
