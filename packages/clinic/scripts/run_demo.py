#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATORY_ROOT = PACKAGE_ROOT.parent
DEFAULT_MODELS_DIR = OBSERVATORY_ROOT / "models"


def count_jsonl(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def max_numeric_delta(actual: dict[str, Any], expected: dict[str, Any]) -> float:
    max_delta = 0.0
    for metric, expected_value in expected["metrics"].items():
        actual_value = actual["metrics"][metric]
        if isinstance(expected_value, dict):
            for key, value in expected_value.items():
                if isinstance(value, int | float):
                    max_delta = max(max_delta, abs(float(actual_value[key]) - float(value)))
        elif isinstance(expected_value, int | float):
            max_delta = max(max_delta, abs(float(actual_value) - float(expected_value)))
    return max_delta


def native_passed(scorecard: dict[str, Any]) -> bool:
    native = scorecard["native"]
    return (
        native["semantic_fallback_count"] == 0
        and native["tool_call_repair_count"] == 0
        and native["tool_name_repair_count"] == 0
        and native["native_text_route_count"] == native["native_text_route_total"]
        and native["native_tool_route_count"] == native["native_tool_route_total"]
    )


def controlled_passed(scorecard: dict[str, Any]) -> bool:
    controlled = scorecard["controlled"]
    return (
        controlled["runtime_gate_passed"]
        and controlled["telemetry_gate_passed"]
        and controlled["behavior_controller_passed"]
        and controlled["route_consistency"] == controlled["route_consistency_total"]
        and not controlled["secret_persistence"]
        and not controlled["raw_payload_persistence"]
    )


def demo_output_dir(raw_output_dir: str | None) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.gettempdir()) / f"wutai-clinic-demo-{timestamp}"


def run_cli(args: list[str], *, output_path: Path | None = None) -> str:
    env = os.environ.copy()
    src_path = str(PACKAGE_ROOT / "src")
    env["PYTHONPATH"] = (
        src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )
    result = subprocess.run(
        [sys.executable, "-m", "wutai_clinic", *args],
        check=True,
        cwd=OBSERVATORY_ROOT.parent,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if output_path is not None:
        output_path.write_text(result.stdout, encoding="utf-8")
    return result.stdout


def require_files(models_dir: Path) -> None:
    required = [
        "trajectories_purified.jsonl",
        "efe_dynamics_report.json",
        "phase311_trajectory_diagnosis_candidates.jsonl",
        "phase316_batch01_uncapped_official_eval_pair_summary.jsonl",
        "phase316_batch02_uncapped_official_eval_pair_summary.jsonl",
        "phase316_cumulative_diagnosis_report.json",
        "phase316_trigger_policy_review_report.json",
        "phase3a_controlled_regression_gate_report.json",
    ]
    missing = [name for name in required if not (models_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required demo model artifacts: {', '.join(missing)}")


def run_demo(models_dir: Path, output_dir: Path, diagnosis_limit: int) -> dict[str, Any]:
    require_files(models_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    purified = models_dir / "trajectories_purified.jsonl"
    ranked = output_dir / "trajectories_hygienic_ranked.jsonl"
    prune_summary = output_dir / "prune_summary.json"
    analysis_report = output_dir / "analysis_report.json"
    diagnosis_candidates = output_dir / f"diagnosis_candidates_{diagnosis_limit}.jsonl"
    audit_report = output_dir / "audit_report.json"
    scorecard_report = output_dir / "scorecard.json"
    closed_loop_dir = output_dir / "closed_loop"

    run_cli(
        [
            "prune",
            str(purified),
            "--no-dedup",
            "--rank",
            "-o",
            str(ranked),
        ],
        output_path=prune_summary,
    )
    run_cli(["analyze", str(purified), "-o", str(analysis_report)])
    run_cli(
        [
            "diagnose",
            str(models_dir / "phase311_trajectory_diagnosis_candidates.jsonl"),
            "--legacy-candidates",
            "--limit",
            str(diagnosis_limit),
            "-o",
            str(diagnosis_candidates),
        ]
    )
    run_cli(["audit", str(models_dir)], output_path=audit_report)
    run_cli(
        [
            "scorecard",
            str(models_dir / "phase3a_controlled_regression_gate_report.json"),
            "-o",
            str(scorecard_report),
        ]
    )
    run_cli(
        [
            "closed-loop",
            str(models_dir / "phase311_trajectory_diagnosis_candidates.jsonl"),
            str(models_dir / "phase316_batch01_uncapped_official_eval_pair_summary.jsonl"),
            str(models_dir / "phase316_batch02_uncapped_official_eval_pair_summary.jsonl"),
            "--cumulative-report",
            str(models_dir / "phase316_cumulative_diagnosis_report.json"),
            "--trigger-policy-review",
            str(models_dir / "phase316_trigger_policy_review_report.json"),
            "-o",
            str(closed_loop_dir),
        ],
        output_path=output_dir / "closed_loop_summary.json",
    )

    prune = load_json(prune_summary)
    analysis = load_json(analysis_report)
    expected_analysis = load_json(models_dir / "efe_dynamics_report.json")
    audit = load_json(audit_report)
    scorecard = load_json(scorecard_report)
    closed_loop = load_json(closed_loop_dir / "closed_loop_evidence_report.json")
    summary = {
        "output_dir": str(output_dir),
        "prune_input_count": prune["input_count"],
        "prune_output_count": prune["output_count"],
        "hygiene_total_filtered": prune["hygiene"]["total_filtered"],
        "hygiene_promotion_gate": prune["hygiene"]["promotion_gate"],
        "ranked_jsonl_count": count_jsonl(ranked),
        "analysis_total_trajectories": analysis["total_trajectories"],
        "analysis_matches_legacy_total": (
            analysis["total_trajectories"] == expected_analysis["total_trajectories"]
        ),
        "analysis_max_numeric_delta_vs_legacy": max_numeric_delta(analysis, expected_analysis),
        "diagnosis_candidate_rows": count_jsonl(diagnosis_candidates),
        "audit_hash_checked": audit["hash_checked"],
        "audit_hash_missing_count": audit["hash_missing_count"],
        "audit_hash_mismatch_count": audit["hash_mismatch_count"],
        "audit_record_count_mismatch_count": audit["record_count_mismatch_count"],
        "audit_hash_consistency_passed": audit["hash_consistency_passed"],
        "scorecard_passed": scorecard["passed"],
        "scorecard_native_passed": native_passed(scorecard),
        "scorecard_controlled_passed": controlled_passed(scorecard),
        "closed_loop_passed": closed_loop["passed"],
        "closed_loop_decision": closed_loop["decision"],
        "closed_loop_gates": closed_loop["gates"],
        "closed_loop_main_treatment_pairs": closed_loop["attribution"]["main_treatment_pairs"],
        "closed_loop_resolved_delta": closed_loop["attribution"]["resolved_delta"],
        "closed_loop_cumulative_selected_pairs": closed_loop.get("cumulative_summary", {}).get(
            "selected_pair_count"
        ),
        "closed_loop_trigger_policy_review_decision": closed_loop.get(
            "trigger_policy_review", {}
        ).get("decision"),
    }
    (output_dir / "demo_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Wutai Clinic five-minute demo.")
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS_DIR))
    parser.add_argument("--output-dir")
    parser.add_argument("--diagnosis-limit", type=int, default=10)
    args = parser.parse_args()

    summary = run_demo(
        models_dir=Path(args.models_dir).expanduser().resolve(),
        output_dir=demo_output_dir(args.output_dir),
        diagnosis_limit=args.diagnosis_limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
