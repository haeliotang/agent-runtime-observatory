#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from wutai_clinic.adapters.sweagent import run_sweagent_fork_preflight  # noqa: E402

UTC = timezone.utc  # py3.10 compat: datetime.UTC is 3.11+


def parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "resolved"}:
        return True
    if normalized in {"false", "0", "no", "unresolved"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false or resolved/unresolved")


def default_output_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.gettempdir()) / f"wutai-sweagent-fork-preflight-{stamp}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the SWE-agent adapter preflight without Docker or provider calls."
    )
    parser.add_argument("--output-dir", default=os.fspath(default_output_dir()))
    parser.add_argument("--control-resolved", type=parse_optional_bool)
    parser.add_argument("--treatment-resolved", type=parse_optional_bool)
    parser.add_argument(
        "--mismatch-model-request",
        action="store_true",
        help="Intentionally change the treatment model_request_hash to verify mismatch blocking.",
    )
    args = parser.parse_args()

    result = run_sweagent_fork_preflight(
        output_dir=Path(args.output_dir).expanduser().resolve(),
        control_resolved=args.control_resolved,
        treatment_resolved=args.treatment_resolved,
        treatment_capsule_overrides={"model_request_hash": "intentional_mismatch"}
        if args.mismatch_model_request
        else None,
    )
    report = result["report"]
    summary = {
        "output_dir": str(Path(args.output_dir).expanduser().resolve()),
        "passed": report["passed"],
        "decision": report["decision"],
        "effect_label": report["effect_label"],
        "report": str(result["report_path"]),
        "manifest": str(result["manifest_path"]),
        "events": str(result["events_path"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
