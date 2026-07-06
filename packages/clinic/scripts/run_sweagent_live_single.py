#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

UTC = timezone.utc  # py3.10 compat: datetime.UTC is 3.11+
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from wutai_clinic.adapters.base import RuntimePermissionPolicy  # noqa: E402
from wutai_clinic.adapters.sweagent_live import (  # noqa: E402
    SWEAgentLiveSingleSpec,
    load_features,
    load_replay_actions,
    run_sweagent_live_single,
)
from wutai_clinic.intervention.paired_fork import default_protocol  # noqa: E402
from wutai_clinic.intervention.replay_protocol import InterventionProtocol, StateCapsule  # noqa: E402


def default_output_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.gettempdir()) / f"wutai-sweagent-live-single-{stamp}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or execute one guarded SWE-agent RunSingle arm."
    )
    parser.add_argument("config", help="SWE-agent RunSingle JSON/YAML config.")
    parser.add_argument("--output-dir", default=os.fspath(default_output_dir()))
    parser.add_argument("--arm", choices=["control", "treatment"], default="control")
    parser.add_argument("--protocol")
    parser.add_argument("--replay-actions")
    parser.add_argument("--features")
    parser.add_argument("--reference-capsule")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-docker", action="store_true")
    parser.add_argument("--ack-external-provider", action="store_true")
    parser.add_argument("--ack-official-eval", action="store_true")
    parser.add_argument("--require-official-eval", action="store_true")
    args = parser.parse_args()

    protocol = (
        InterventionProtocol.from_file(Path(args.protocol))
        if args.protocol
        else default_protocol()
    )
    reference = (
        StateCapsule.from_file(Path(args.reference_capsule))
        if args.reference_capsule
        else None
    )
    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=Path(args.config).expanduser().resolve(),
            output_dir=Path(args.output_dir).expanduser().resolve(),
            arm_type=args.arm,
            execute=args.execute,
            protocol=protocol,
            replay_actions=load_replay_actions(Path(args.replay_actions))
            if args.replay_actions
            else load_replay_actions(None),
            features=load_features(Path(args.features)) if args.features else load_features(None),
            reference_capsule=reference,
            require_official_eval=args.require_official_eval,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=args.ack_docker,
            allow_external_provider=args.ack_external_provider,
            allow_official_eval=args.ack_official_eval,
        ),
    )
    report = result["report"]
    summary = {
        "decision": report["decision"],
        "passed": report["passed"],
        "arm_type": report["arm_type"],
        "execute_requested": report["execute_requested"],
        "run_single_started": report["run_single_started"],
        "report": str(result["report_path"]),
        "manifest": str(result["manifest_path"]),
        "events": str(result["events_path"]),
        "capsule": str(result["capsule_path"]) if result["capsule_path"] else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
