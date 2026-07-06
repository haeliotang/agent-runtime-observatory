#!/usr/bin/env python3
"""Durable repaired-substrate wrapper for local SWE-bench evaluation runs.

Extends ``models/run_phase310_swebench_compat.py`` (legacy packaging compat)
with the roman fix: sphinx's LaTeX builder imports ``roman`` (formerly
vendored as ``docutils.utils.roman``), but the env images resolve a docutils
without it, killing every sphinx test session at setup and forcing all
official outcomes to unresolved (see
``instance_validity/substrate_repair_note.json``).

Patching the spec's ``pip_packages`` bakes the dependency into the generated
env-image build script, so the fix survives any image rebuild — unlike the
2026-06-12 ``docker commit`` repair, which was silently lost when the local
image cache was recycled.

Usage: identical CLI to ``swebench.harness.run_evaluation``.
"""

from __future__ import annotations

import runpy
from pathlib import Path

ROMAN_PIN = "roman==4.2"

_COMPAT_WRAPPER = Path(__file__).resolve().parents[2] / "models" / "run_phase310_swebench_compat.py"


def _load_compat_module() -> dict[str, object]:
    return runpy.run_path(_COMPAT_WRAPPER.as_posix())


def apply_sphinx_roman_fix() -> None:
    import swebench.harness.constants as constants
    import swebench.harness.constants.python as python_constants

    for specs in (
        python_constants.SPECS_SPHINX,
        constants.MAP_REPO_VERSION_TO_SPECS.get("sphinx-doc/sphinx", {}),
    ):
        for spec in specs.values():
            pip_packages = list(spec.get("pip_packages") or [])
            if not any(pkg.split("==")[0] == "roman" for pkg in pip_packages):
                pip_packages.append(ROMAN_PIN)
            spec["pip_packages"] = pip_packages


def main() -> None:
    compat = _load_compat_module()
    compat["apply_legacy_python_packaging_compat"]()
    apply_sphinx_roman_fix()
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")


if __name__ == "__main__":
    main()
