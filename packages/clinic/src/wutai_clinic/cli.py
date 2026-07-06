from __future__ import annotations

import json
from dataclasses import asdict
from itertools import islice
from pathlib import Path
from typing import Optional

import typer

from wutai_clinic.engine.diagnoser import diagnose as diagnose_trajectory
from wutai_clinic.engine.pruner import prune_corpus
from wutai_clinic.engine.scorer import dual_scorecard_from_phase3a_report, score_suite
from wutai_clinic.engine.trajectory_analyzer import analyze_corpus
from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent import run_sweagent_fork_preflight, sweagent_live_plan_report
from wutai_clinic.adapters.sweagent_live import (
    SWEAgentLiveSingleSpec,
    load_features,
    load_replay_actions,
    run_sweagent_live_single,
)
from wutai_clinic.adapters.sweagent_official_pair import (
    SWEAgentOfficialPairSpec,
    run_sweagent_official_pair,
)
from wutai_clinic.adapters.sweagent_phase6_official_eval import (
    SWEAgentPhase6OfficialEvalSpec,
    run_sweagent_phase6_official_eval,
)
from wutai_clinic.adapters.sweagent_protocol_v1_preflight import (
    write_sweagent_protocol_v1_preflight_evidence,
)
from wutai_clinic.adapters.sweagent_protocol_v1_live import (
    SWEAgentProtocolV1LiveSingleSpec,
    run_sweagent_protocol_v1_live_single,
)
from wutai_clinic.adapters.sweagent_protocol_v1_runtime import (
    SWEAgentProtocolV1RuntimeConfigSpec,
    activate_sweagent_protocol_v1_runtime_config,
)
from wutai_clinic.adapters.sweagent_protocol_v1_pair import (
    SWEAgentProtocolV1LivePairSpec,
    run_sweagent_protocol_v1_live_pair,
)
from wutai_clinic.adapters.sweagent_protocol_v1_official_eval import (
    SWEAgentProtocolV1OfficialEvalSpec,
    run_sweagent_protocol_v1_official_eval,
)
from wutai_clinic.adapters.sweagent_protocol_v2_live import (
    SWEAgentProtocolV2LiveSingleSpec,
    run_sweagent_protocol_v2_live_single,
)
from wutai_clinic.adapters.sweagent_protocol_v2_pair import (
    SWEAgentProtocolV2LivePairSpec,
    run_sweagent_protocol_v2_live_pair,
)
from wutai_clinic.adapters.sweagent_protocol_v2_official_eval import (
    SWEAgentProtocolV2OfficialEvalSpec,
    run_sweagent_protocol_v2_official_eval,
)
from wutai_clinic.adapters.sweagent_live_pair import (
    SWEAgentLivePairSpec,
    run_sweagent_live_pair,
)
from wutai_clinic.adapters.sweagent_live_preflight import (
    SWEAgentLiveHookPreflightSpec,
    run_sweagent_live_hook_preflight,
)
from wutai_clinic.intervention.attribution import attribute_pair_summaries
from wutai_clinic.intervention.batch_readiness import write_batch3_readiness_evidence
from wutai_clinic.intervention.closed_loop import write_closed_loop_evidence
from wutai_clinic.intervention.paired_fork import default_protocol
from wutai_clinic.intervention.planner import build_package_rows
from wutai_clinic.intervention.protocol_v1_dry_run import (
    write_protocol_v1_dry_run_evidence,
)
from wutai_clinic.intervention.protocol_v1_hook_preflight import (
    write_protocol_v1_hook_preflight_evidence,
)
from wutai_clinic.intervention.protocol_v1_fresh_candidates import (
    write_protocol_v1_fresh_candidate_evidence,
)
from wutai_clinic.intervention.protocol_v1 import ProtocolV1
from wutai_clinic.intervention.protocol_v1_batch_outcomes import (
    write_protocol_v1_batch_outcomes_evidence,
)
from wutai_clinic.intervention.protocol_v2 import ProtocolV2, protocol_v2_prescription_template
from wutai_clinic.intervention.protocol_v2_dry_run import (
    write_protocol_v2_dry_run_evidence,
)
from wutai_clinic.intervention.protocol_v2_fresh_candidates import (
    write_protocol_v2_fresh_candidate_evidence,
)
from wutai_clinic.intervention.protocol_v2_pair_inputs import (
    write_protocol_v2_pair_inputs_evidence,
)
from wutai_clinic.intervention.protocol_v2_planned_preflight import (
    write_protocol_v2_planned_preflight_evidence,
)
from wutai_clinic.intervention.route_b1 import (
    write_route_b1_antileak_evidence,
    write_route_b1_plan_evidence,
)
from wutai_clinic.intervention.b1_issue_repro import build_b1_payload, issue_repro_eligibility
from wutai_clinic.intervention.protocol_b1 import ProtocolB1, protocol_b1_template
from wutai_clinic.adapters.sweagent_b1_live import (
    B1LeakRefs,
    SWEAgentB1LiveSingleSpec,
    run_sweagent_b1_live_single,
)
from wutai_clinic.intervention.route_b1_decision import (
    aggregate_cells_to_anchor_outcomes,
    route_b1_decision,
)
from wutai_clinic.intervention.route_b1_cells import (
    assemble_cells,
    discover_arm_reports,
    resolved_map_from_labels,
)
from wutai_clinic.intervention.replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    protocol_check_report,
)
from wutai_clinic.intervention.stability import write_batch_stability_evidence
from wutai_clinic.io import count_jsonl, read_jsonl, write_jsonl
from wutai_clinic.schemas import Report, Trajectory, TrajectoryDiagnosis
from wutai_clinic.evidence.inventory import (
    _audit_manifest_hashes,
    write_evidence_index,
)
from wutai_clinic.engine.power import write_power_report
from wutai_clinic.intervention.protocol_v2_batch_outcomes import (
    write_protocol_v2_batch_outcomes_evidence,
)
from wutai_clinic.intervention.fresh_target_harvest import (
    run_fresh_target_harvest,
    write_fresh_target_harvest_plan,
)
from wutai_clinic.orchestration.batch_runner import advance_batch, batch_status
from wutai_clinic.workflow_doctor import diagnose_workflow

app = typer.Typer(
    name="wutai-clinic",
    help="Agent trajectory diagnostics and behavioral-control experiment platform.",
    no_args_is_help=True,
)


def _emit_json(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json_or_jsonl(path: Path) -> object:
    if path.suffix == ".jsonl":
        return list(read_jsonl(path))
    return json.loads(path.read_text())


def _parse_optional_bool(value: Optional[str], *, name: str) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "resolved"}:
        return True
    if normalized in {"false", "0", "no", "unresolved"}:
        return False
    raise typer.BadParameter(f"{name} must be true/false or resolved/unresolved")


def _resolve_artifact_path(evidence_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    base = evidence_dir.resolve()
    candidates = [
        Path.cwd() / path,
        base / path,
        base.parent / path,
        base.parent.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]



@app.command()
def doctor(
    evidence_dir: Path = typer.Argument(..., help="Observatory artifact directory to scan."),
    planning_budget: float = typer.Option(0.30, help="Maximum planning share before warning."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Flag planning/gate buildup before it displaces experiments."""
    report = diagnose_workflow(evidence_dir, planning_budget=planning_budget)
    if json_output:
        _emit_json(report.to_dict())
        return
    typer.echo(f"decision: {report.decision}")
    typer.echo(f"artifacts: {report.artifact_count}")
    typer.echo(f"planning artifacts: {report.planning_artifacts}")
    typer.echo(f"experiment artifacts: {report.experiment_artifacts}")
    typer.echo(f"planning ratio: {report.planning_ratio:.3f} (budget {report.planning_budget:.2f})")
    if report.warnings:
        typer.echo(f"warnings: {', '.join(report.warnings)}")
    typer.echo(f"next action: {report.next_action}")


@app.command()
def diagnose(
    input: Path = typer.Argument(..., help="Trajectory JSONL or legacy diagnosis JSONL."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSONL path."),
    limit: Optional[int] = typer.Option(None, help="Only process the first N rows."),
    legacy_candidates: bool = typer.Option(
        False,
        help="Parse input as existing Phase 3.11 diagnosis candidates instead of trajectories.",
    ),
) -> None:
    """Diagnose trajectories or normalize legacy diagnosis candidates."""
    rows = []
    for index, row in enumerate(read_jsonl(input), start=1):
        if limit is not None and index > limit:
            break
        if legacy_candidates:
            rows.append(TrajectoryDiagnosis.from_dict(row).to_dict())
        else:
            rows.append(diagnose_trajectory(Trajectory.from_dict(row)).to_dict())
    if output:
        write_jsonl(output, rows)
    else:
        _emit_json(rows)


@app.command()
def analyze(
    input: Path = typer.Argument(..., help="Trajectory JSONL."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON report path."),
    limit: Optional[int] = typer.Option(None, help="Only process the first N rows."),
) -> None:
    """Compute lightweight trajectory metrics."""
    if limit is None:
        report = analyze_corpus(input)
    else:
        trajectories = []
        for index, row in enumerate(read_jsonl(input), start=1):
            if index > limit:
                break
            trajectories.append(Trajectory.from_dict(row))
        report = analyze_corpus(trajectories)
    if output:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        _emit_json(report)


@app.command()
def prune(
    input: Path = typer.Argument(..., help="Trajectory JSONL."),
    output: Path = typer.Option(..., "--output", "-o", help="Output pruned JSONL path."),
    limit: Optional[int] = typer.Option(None, help="Only process the first N rows."),
    no_dedup: bool = typer.Option(False, "--no-dedup", help="Skip task-level deduplication."),
    rank: bool = typer.Option(False, "--rank", help="Rank by STR health / quality score."),
    target_hygiene: bool = typer.Option(
        True,
        "--target-hygiene/--no-target-hygiene",
        help="Apply the Phase 2.6 target hygiene gate before pruning.",
    ),
) -> None:
    """Prune loops/restarts and optionally deduplicate trajectories."""
    source_rows = []
    for index, row in enumerate(read_jsonl(input), start=1):
        if limit is not None and index > limit:
            break
        source_rows.append(row)
    trajectories, stats, hygiene_result = prune_corpus(
        source_rows,
        target_hygiene=target_hygiene,
        dedup=not no_dedup,
        rank=rank,
        input_file=str(input),
        output_file=str(output),
    )
    write_jsonl(output, [trajectory.to_dict() for trajectory in trajectories])
    payload = asdict(stats)
    if hygiene_result is not None:
        payload["hygiene"] = {
            "version": hygiene_result.manifest["version"],
            "total_raw": hygiene_result.manifest["total_raw"],
            "total_purified": hygiene_result.manifest["total_purified"],
            "total_filtered": hygiene_result.manifest["total_filtered"],
            "promotion_gate": hygiene_result.manifest["promotion_gate"],
        }
    _emit_json(payload)


@app.command()
def scorecard(
    input: Path = typer.Argument(..., help="Phase 3A report JSON or response JSON/JSONL."),
    eval_suite: Optional[Path] = typer.Option(None, "--eval-suite", help="Eval suite JSON/JSONL."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON path."),
    table: bool = typer.Option(False, "--table", help="Emit human-readable table."),
) -> None:
    """Compute native/controlled scorecards."""
    if eval_suite:
        responses = _load_json_or_jsonl(input)
        suite = _load_json_or_jsonl(eval_suite)
        if not isinstance(responses, list) or not isinstance(suite, list):
            raise typer.BadParameter("response and eval suite inputs must be JSON arrays or JSONL")
        scorecard_result = score_suite(responses, suite)
    else:
        report = json.loads(input.read_text())
        scorecard_result = dual_scorecard_from_phase3a_report(report)
    if table:
        typer.echo(scorecard_result.to_table())
        return
    payload = asdict(scorecard_result)
    payload["passed"] = scorecard_result.passed
    if output:
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        _emit_json(payload)


@app.command()
def intervene(
    input: Path = typer.Argument(..., help="Diagnosis candidates or pair summary JSONL."),
    output: Path = typer.Option(
        ..., "--output", "-o", help="Output dry-run plan or attribution JSON path."
    ),
    mode: str = typer.Option("plan", "--mode", help="plan or attribute."),
    limit: Optional[int] = typer.Option(None, help="Only process the first N rows."),
    ack_external: bool = typer.Option(
        False, "--ack-external", help="Required for real external execution."
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--real-run", help="Never call external APIs in dry-run mode."
    ),
) -> None:
    """Plan intervention arms or aggregate dry-run attribution."""
    if not dry_run and not ack_external:
        raise typer.BadParameter("--ack-external is required with --real-run")
    rows = list(islice(read_jsonl(input), limit)) if limit is not None else list(read_jsonl(input))
    if mode == "attribute":
        report = attribute_pair_summaries(rows)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _emit_json({"mode": "attribute", "output": str(output), "pair_count": len(rows)})
        return
    if mode != "plan":
        raise typer.BadParameter("mode must be 'plan' or 'attribute'")
    _, arms = build_package_rows(rows)
    write_jsonl(output, arms)
    _emit_json({"mode": "plan", "output": str(output), "arm_count": len(arms), "dry_run": dry_run})


@app.command("closed-loop")
def closed_loop(
    diagnoses: Path = typer.Argument(..., help="Phase 3.11 diagnosis candidates JSONL."),
    pair_summaries: list[Path] = typer.Argument(..., help="Official eval pair summary JSONL files."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    cumulative_report: Optional[Path] = typer.Option(
        None, "--cumulative-report", help="Optional cumulative diagnosis report JSON."
    ),
    trigger_policy_review: Optional[Path] = typer.Option(
        None, "--trigger-policy-review", help="Optional trigger policy review report JSON."
    ),
) -> None:
    """Generate behavior-control closed-loop evidence artifacts."""
    candidate_rows = list(read_jsonl(diagnoses))
    pair_rows = [row for path in pair_summaries for row in read_jsonl(path)]
    cumulative_payload = json.loads(cumulative_report.read_text()) if cumulative_report else None
    trigger_review_payload = (
        json.loads(trigger_policy_review.read_text()) if trigger_policy_review else None
    )
    result = write_closed_loop_evidence(
        candidate_rows=candidate_rows,
        pair_summary=pair_rows,
        output_dir=output_dir,
        input_artifacts=[
            path
            for path in [diagnoses, *pair_summaries, cumulative_report, trigger_policy_review]
            if path is not None
        ],
        cumulative_report=cumulative_payload,
        trigger_policy_review=trigger_review_payload,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "gates": report["gates"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("batch-stability")
def batch_stability(
    pair_summaries: list[Path] = typer.Argument(..., help="Official eval pair summary JSONL files."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    target_main_pairs: int = typer.Option(
        4,
        "--target-main-pairs",
        help="Target main attribution pair count before any stability claim.",
    ),
    min_total_pairs: int = typer.Option(
        4, "--min-total-pairs", help="Minimum total completed pair count for a small-batch probe."
    ),
    max_total_pairs: int = typer.Option(
        8, "--max-total-pairs", help="Maximum total completed pair count for this probe."
    ),
) -> None:
    """Summarize multi-pair official outcomes for small-batch stability."""
    pair_rows = [row for path in pair_summaries for row in read_jsonl(path)]
    result = write_batch_stability_evidence(
        pair_summary=pair_rows,
        output_dir=output_dir,
        input_artifacts=pair_summaries,
        target_main_pairs=target_main_pairs,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
    )
    report = result["report"]
    summary = report["stability_summary"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "summary": str(result["summary_path"]),
            "total_pair_count": summary["total_pair_count"],
            "main_treatment_pair_count": summary["main_treatment_pair_count"],
            "positive_main_count": summary["positive_main_count"],
            "neutral_main_count": summary["neutral_main_count"],
            "negative_main_count": summary["negative_main_count"],
        }
    )


@app.command("batch3-readiness")
def batch3_readiness(
    stability_report: Path = typer.Argument(..., help="Batch stability report JSON."),
    trigger_policy_review: Path = typer.Argument(..., help="Trigger policy review report JSON."),
    recalibration_report: Path = typer.Argument(..., help="Live-trigger recalibration report JSON."),
    recalibration_protocol: Path = typer.Argument(
        ..., help="Live-trigger recalibration protocol JSON."
    ),
    candidates: Path = typer.Argument(..., help="Batch-3 recalibration candidate JSONL."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    live_feature_dry_run_report: Optional[Path] = typer.Option(
        None,
        "--live-feature-dry-run-report",
        help="Optional live-feature hook dry-run report JSON.",
    ),
) -> None:
    """Gate batch-3 expansion readiness without authorizing a real run."""
    dry_run_payload = (
        json.loads(live_feature_dry_run_report.read_text())
        if live_feature_dry_run_report
        else None
    )
    input_artifacts = [
        stability_report,
        trigger_policy_review,
        recalibration_report,
        recalibration_protocol,
        candidates,
    ]
    if live_feature_dry_run_report is not None:
        input_artifacts.append(live_feature_dry_run_report)
    result = write_batch3_readiness_evidence(
        stability_report=json.loads(stability_report.read_text()),
        trigger_policy_review=json.loads(trigger_policy_review.read_text()),
        recalibration_report=json.loads(recalibration_report.read_text()),
        recalibration_protocol=json.loads(recalibration_protocol.read_text()),
        candidate_rows=list(read_jsonl(candidates)),
        live_feature_dry_run_report=dry_run_payload,
        output_dir=output_dir,
        input_artifacts=input_artifacts,
    )
    report = result["report"]
    summary = report["readiness_summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "summary": str(result["summary_path"]),
            "candidate_count": summary["candidate_count"],
            "dry_run_present": summary["dry_run_present"],
            "allow_live_hook_runner_preflight": policy["allow_live_hook_runner_preflight"],
            "allow_batch3_real_run": policy["allow_batch3_real_run"],
        }
    )


@app.command("batch3-live-hook-preflight")
def batch3_live_hook_preflight(
    readiness_report: Path = typer.Argument(..., help="Batch-3 readiness report JSON."),
    candidates: Path = typer.Argument(..., help="Batch-3 recalibration candidate JSONL."),
    run_single_config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    pair_id: Optional[str] = typer.Option(None, "--pair-id", help="Candidate pair id to run."),
    replay_actions: Optional[Path] = typer.Option(
        None,
        "--replay-actions",
        help="Replay actions JSON/YAML. Required for --execute.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Run one control/treatment live-hook preflight pair.",
    ),
    ack_docker: bool = typer.Option(
        False, "--ack-docker", help="Acknowledge that execute mode may start Docker."
    ),
    ack_external_provider: bool = typer.Option(
        False,
        "--ack-external-provider",
        help="Acknowledge that execute mode may call a model provider.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge official-eval outcome import if required later.",
    ),
    require_official_eval: bool = typer.Option(
        False,
        "--require-official-eval",
        help="Require official-eval acknowledgement in the execution gate.",
    ),
) -> None:
    """Prepare or execute one batch-3 live-hook runner preflight pair."""
    result = run_sweagent_live_hook_preflight(
        spec=SWEAgentLiveHookPreflightSpec(
            readiness_report=json.loads(readiness_report.read_text()),
            candidate_rows=list(read_jsonl(candidates)),
            run_single_config=run_single_config,
            output_dir=output_dir,
            pair_id=pair_id,
            replay_actions_path=replay_actions,
            execute=execute,
            require_official_eval=require_official_eval,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
    )
    report = result["report"]
    summary = report["execution_summary"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "pair_id": report["pair_id"],
            "source_task_id": report["source_task_id"],
            "execute_requested": summary["execute_requested"],
            "control_started": summary["control_started"],
            "treatment_started": summary["treatment_started"],
            "pair_decision": summary["pair_decision"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "commands": str(result["commands_path"]),
        }
    )


@app.command("protocol-check")
def protocol_check(
    protocol: Path = typer.Argument(..., help="Protocol v0 JSON/YAML file."),
    control_capsule: Path = typer.Argument(..., help="Control State Capsule JSON/YAML file."),
    treatment_capsule: Path = typer.Argument(..., help="Treatment State Capsule JSON/YAML file."),
    feature_windows: Optional[Path] = typer.Option(
        None, "--feature-windows", help="Optional JSON/JSONL feature windows for dry simulation."
    ),
    control_resolved: Optional[str] = typer.Option(
        None, "--control-resolved", help="Optional control outcome: true/false or resolved/unresolved."
    ),
    treatment_resolved: Optional[str] = typer.Option(
        None, "--treatment-resolved", help="Optional treatment outcome: true/false or resolved/unresolved."
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON report path."),
) -> None:
    """Validate Protocol v0 and State Capsule fork equivalence."""
    protocol_payload = InterventionProtocol.from_file(protocol)
    control_payload = StateCapsule.from_file(control_capsule)
    treatment_payload = StateCapsule.from_file(treatment_capsule)
    windows: list[dict] | None = None
    if feature_windows is not None:
        loaded = _load_json_or_jsonl(feature_windows)
        if not isinstance(loaded, list) or not all(isinstance(row, dict) for row in loaded):
            raise typer.BadParameter("--feature-windows must be a JSON/JSONL list of objects")
        windows = loaded
    report = protocol_check_report(
        protocol=protocol_payload,
        control_capsule=control_payload,
        treatment_capsule=treatment_payload,
        feature_windows=windows,
        control_resolved=_parse_optional_bool(control_resolved, name="--control-resolved"),
        treatment_resolved=_parse_optional_bool(treatment_resolved, name="--treatment-resolved"),
    )
    if output is not None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        _emit_json(report)


@app.command("protocol-v1-dry-run")
def protocol_v1_dry_run(
    protocol_v1_plan: Path = typer.Argument(..., help="Protocol v1 plan JSON."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
) -> None:
    """Dry-run Protocol v1 prescriptions without authorizing live execution."""
    result = write_protocol_v1_dry_run_evidence(
        protocol_v1_plan=json.loads(protocol_v1_plan.read_text()),
        output_dir=output_dir,
        input_artifacts=[protocol_v1_plan],
    )
    report = result["report"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "summary": str(result["summary_path"]),
            "allow_protocol_v1_live_hook_adapter_preflight": policy[
                "allow_protocol_v1_live_hook_adapter_preflight"
            ],
            "allow_protocol_v1_real_run": policy["allow_protocol_v1_real_run"],
        }
    )


@app.command("protocol-v1-sweagent-preflight")
def protocol_v1_sweagent_preflight(
    protocol_v1_plan: Path = typer.Argument(..., help="Protocol v1 plan JSON."),
    dry_run_report: Path = typer.Argument(..., help="Protocol v1 dry-run report JSON."),
    run_single_config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
) -> None:
    """Preflight Protocol v1 SWE-agent adapter enforcement without live execution."""
    result = write_sweagent_protocol_v1_preflight_evidence(
        protocol_v1_plan=json.loads(protocol_v1_plan.read_text()),
        dry_run_report=json.loads(dry_run_report.read_text()),
        run_single_config=run_single_config,
        output_dir=output_dir,
        input_artifacts=[protocol_v1_plan, dry_run_report, run_single_config],
    )
    report = result["report"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "commands": str(result["commands_path"]),
            "summary": str(result["summary_path"]),
            "allow_protocol_v1_constraint_hook_implementation": policy[
                "allow_protocol_v1_constraint_hook_implementation"
            ],
            "allow_protocol_v1_real_run": policy["allow_protocol_v1_real_run"],
        }
    )


@app.command("protocol-v1-hook-preflight")
def protocol_v1_hook_preflight(
    protocol_v1_plan: Path = typer.Argument(..., help="Protocol v1 plan JSON."),
    adapter_preflight_report: Path = typer.Argument(
        ..., help="Protocol v1 SWE-agent adapter preflight report JSON."
    ),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
) -> None:
    """Run the controlled Protocol v1 constraint-hook harness without live execution."""
    result = write_protocol_v1_hook_preflight_evidence(
        protocol_v1_plan=json.loads(protocol_v1_plan.read_text()),
        adapter_preflight_report=json.loads(adapter_preflight_report.read_text()),
        output_dir=output_dir,
        input_artifacts=[protocol_v1_plan, adapter_preflight_report],
    )
    report = result["report"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "summary": str(result["summary_path"]),
            "allow_protocol_v1_live_single_adapter_integration": policy[
                "allow_protocol_v1_live_single_adapter_integration"
            ],
            "allow_protocol_v1_real_run": policy["allow_protocol_v1_real_run"],
        }
    )


@app.command("protocol-v1-fresh-candidates")
def protocol_v1_fresh_candidates(
    eligible_refs: Path = typer.Argument(..., help="Low-nondeterminism eligible refs JSONL."),
    candidate_pool_report: Path = typer.Argument(..., help="Candidate pool report JSON."),
    protocol_v1_plan: Path = typer.Argument(..., help="Existing Protocol v1 plan JSON."),
    no_uplift_diagnosis: Path = typer.Argument(..., help="No-uplift diagnosis JSON."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    target_pair_count: int = typer.Option(
        4,
        "--target-pair-count",
        help="Fresh failure-target pair count required for full-batch planned preflight.",
    ),
) -> None:
    """Gate fresh Protocol v1 candidates after excluding same-pair posthoc evidence."""
    result = write_protocol_v1_fresh_candidate_evidence(
        eligible_refs=list(read_jsonl(eligible_refs)),
        candidate_pool_report=json.loads(candidate_pool_report.read_text()),
        protocol_v1_plan=json.loads(protocol_v1_plan.read_text()),
        no_uplift_diagnosis=json.loads(no_uplift_diagnosis.read_text()),
        output_dir=output_dir,
        target_pair_count=target_pair_count,
        input_artifacts=[
            eligible_refs,
            candidate_pool_report,
            protocol_v1_plan,
            no_uplift_diagnosis,
        ],
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "fresh_candidates": str(result["fresh_path"]),
            "excluded_candidates": str(result["excluded_path"]),
            "summary": str(result["summary_path"]),
            "fresh_candidate_count": summary["fresh_candidate_count"],
            "fresh_failure_target_count": summary["fresh_failure_target_count"],
            "excluded_candidate_count": summary["excluded_candidate_count"],
            "allow_protocol_v1_live_single_planned_preflight": policy[
                "allow_protocol_v1_live_single_planned_preflight"
            ],
            "allow_protocol_v1_full_batch_planned_preflight": policy[
                "allow_protocol_v1_full_batch_planned_preflight"
            ],
            "allow_protocol_v1_real_run": policy["allow_protocol_v1_real_run"],
        }
    )


@app.command("protocol-v1-batch-outcomes")
def protocol_v1_batch_outcomes(
    root: Path = typer.Argument(..., help="Phase 6 Protocol v1 evidence root."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    target_pair_count: int = typer.Option(
        4,
        "--target-pair-count",
        help="Protocol v1 official-eval pair count required before a stability claim.",
    ),
    include_v0_reference: bool = typer.Option(
        True,
        "--include-v0-reference/--no-v0-reference",
        help="Include v0 state-capsule official-eval pairs as separately stratified context.",
    ),
) -> None:
    """Aggregate Protocol v1 official outcomes without mixing v0 reference evidence."""
    result = write_protocol_v1_batch_outcomes_evidence(
        root=root,
        output_dir=output_dir,
        include_v0_reference=include_v0_reference,
        target_protocol_v1_pair_count=target_pair_count,
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "summary": str(result["summary_path"]),
            "protocol_v1_pair_count": summary["protocol_v1_pair_count"],
            "protocol_v1_positive_count": summary["protocol_v1_positive_count"],
            "protocol_v1_no_uplift_count": summary["protocol_v1_no_uplift_count"],
            "protocol_v1_negative_count": summary["protocol_v1_negative_count"],
            "v0_reference_pair_count": summary["v0_reference_pair_count"],
            "allow_more_protocol_v1_pairs": policy["allow_more_protocol_v1_pairs"],
            "allow_protocol_v2_prescription_design": policy[
                "allow_protocol_v2_prescription_design"
            ],
            "recommended_next_step": policy["recommended_next_step"],
        }
    )


@app.command("protocol-v2-prescription-template")
def protocol_v2_template(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON path."),
    prescription_id: str = typer.Option(
        "break_recurrence_and_reproduce",
        "--prescription-id",
        help="Protocol v2 prescription id.",
    ),
) -> None:
    """Emit a guarded Protocol v2 prescription template without live execution."""
    protocol = protocol_v2_prescription_template(prescription_id=prescription_id)
    payload = {
        "decision": "protocol_v2_prescription_template_ready_not_live_executed",
        "protocol_hash": protocol.protocol_hash,
        "protocol_v2": protocol.to_dict(),
    }
    if output is not None:
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _emit_json({"decision": payload["decision"], "output": str(output)})
        return
    _emit_json(payload)


@app.command("protocol-v2-dry-run")
def protocol_v2_dry_run(
    batch_outcomes_report: Path = typer.Argument(..., help="Protocol v1 batch outcomes report JSON."),
    outcome_rows: Path = typer.Argument(..., help="Protocol v1 batch outcome pairs JSONL."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    protocol_v2_template_path: Optional[Path] = typer.Option(
        None,
        "--protocol-v2-template",
        help="Optional Protocol v2 template JSON. Defaults to break_recurrence_and_reproduce.",
    ),
) -> None:
    """Dry-run a Protocol v2 prescription plan from Protocol v1 no-uplift dynamics."""
    if protocol_v2_template_path is None:
        protocol = protocol_v2_prescription_template()
        input_artifacts = [batch_outcomes_report, outcome_rows]
    else:
        payload = json.loads(protocol_v2_template_path.read_text())
        protocol_payload = payload.get("protocol_v2") if isinstance(payload, dict) else None
        protocol = ProtocolV2.from_dict(protocol_payload or payload)
        input_artifacts = [batch_outcomes_report, outcome_rows, protocol_v2_template_path]
    result = write_protocol_v2_dry_run_evidence(
        batch_outcomes_report=json.loads(batch_outcomes_report.read_text()),
        outcome_rows=list(read_jsonl(outcome_rows)),
        protocol_v2=protocol,
        output_dir=output_dir,
        input_artifacts=input_artifacts,
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "plan": str(result["plan_path"]),
            "events": str(result["events_path"]),
            "target_pair_count": summary["target_pair_count"],
            "allow_protocol_v2_live_single_planned_preflight": policy[
                "allow_protocol_v2_live_single_planned_preflight"
            ],
            "allow_protocol_v2_real_run": policy["allow_protocol_v2_real_run"],
            "recommended_next_step": policy["recommended_next_step"],
        }
    )


@app.command("protocol-v2-fresh-candidates")
def protocol_v2_fresh_candidates(
    candidate_sources: list[Path] = typer.Argument(..., help="Candidate source JSONL files."),
    protocol_v2_dry_run_report: Path = typer.Option(
        ...,
        "--protocol-v2-dry-run-report",
        help="Protocol v2 dry-run report JSON.",
    ),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    official_eval_root: Path = typer.Option(
        Path("../models"),
        "--official-eval-root",
        help="Root to scan for completed official-eval scorecards and mismatch audits.",
    ),
    pair_input_root: list[Path] = typer.Option(
        [],
        "--pair-input-root",
        help="Root containing per-task candidate/replay/config input directories.",
    ),
    target_pair_count: int = typer.Option(4, "--target-pair-count"),
) -> None:
    """Select fresh Protocol v2 failure targets after excluding contaminated pairs."""
    candidate_rows = [row for path in candidate_sources for row in read_jsonl(path)]
    result = write_protocol_v2_fresh_candidate_evidence(
        candidate_rows=candidate_rows,
        protocol_v2_dry_run_report=json.loads(protocol_v2_dry_run_report.read_text()),
        official_eval_roots=[official_eval_root],
        pair_input_roots=list(pair_input_root),
        output_dir=output_dir,
        input_artifacts=[*candidate_sources, protocol_v2_dry_run_report],
        target_pair_count=target_pair_count,
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "fresh_candidates": str(result["fresh_path"]),
            "excluded_candidates": str(result["excluded_path"]),
            "fresh_candidate_count": summary["fresh_candidate_count"],
            "fresh_failure_target_count": summary["fresh_failure_target_count"],
            "excluded_candidate_count": summary["excluded_candidate_count"],
            "allow_protocol_v2_planned_preflight": policy[
                "allow_protocol_v2_planned_preflight"
            ],
            "allow_protocol_v2_real_run": policy["allow_protocol_v2_real_run"],
            "recommended_next_step": policy["recommended_next_step"],
        }
    )


@app.command("protocol-v2-materialize-pair-inputs")
def protocol_v2_materialize_pair_inputs(
    candidate_sources: list[Path] = typer.Argument(..., help="Candidate source JSONL files."),
    output_root: Path = typer.Option(..., "--output-root", help="Per-task input output root."),
    native_root: Path = typer.Option(..., "--native-root", help="Per-task native run output root."),
    trajectory_root: Path = typer.Option(
        Path("../software-agent-sdk-main/swe_agent_src/trajectories"),
        "--trajectory-root",
        help="SWE-agent trajectory root containing task .traj files.",
    ),
    pair_id: list[str] = typer.Option([], "--pair-id", help="Pair id to materialize."),
) -> None:
    """Materialize Protocol v2 replay/config inputs without live execution."""
    candidate_rows = [row for path in candidate_sources for row in read_jsonl(path)]
    result = write_protocol_v2_pair_inputs_evidence(
        candidate_rows=candidate_rows,
        trajectory_root=trajectory_root,
        output_root=output_root,
        native_root=native_root,
        pair_ids=list(pair_id),
    )
    report = result["report"]
    summary = report["summary"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_root": str(output_root),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "candidate_row_count": summary["candidate_row_count"],
            "materialized_count": summary["materialized_count"],
            "ready_count": summary["ready_count"],
            "low_or_no_replay_risk_count": summary["low_or_no_replay_risk_count"],
            "failed_count": summary["failed_count"],
        }
    )


@app.command("protocol-v2-planned-preflight")
def protocol_v2_planned_preflight(
    candidate_set_report: Path = typer.Argument(..., help="Protocol v2 candidate report JSON."),
    candidate_rows: Path = typer.Argument(..., help="Protocol v2 candidate rows JSONL."),
    protocol_v2_template_path: Path = typer.Argument(..., help="Protocol v2 template JSON."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    source_task_id: Optional[str] = typer.Option(None, "--source-task-id"),
    model_name: Optional[str] = typer.Option(None, "--model-name"),
    api_base: Optional[str] = typer.Option(None, "--api-base"),
) -> None:
    """Preflight the first Protocol v2 fresh target without live execution."""
    payload = json.loads(protocol_v2_template_path.read_text())
    protocol_payload = payload.get("protocol_v2") if isinstance(payload, dict) else None
    protocol = ProtocolV2.from_dict(protocol_payload or payload)
    result = write_protocol_v2_planned_preflight_evidence(
        candidate_set_report=json.loads(candidate_set_report.read_text()),
        candidate_rows=list(read_jsonl(candidate_rows)),
        protocol_v2=protocol,
        output_dir=output_dir,
        source_task_id=source_task_id,
        model_name=model_name,
        api_base=api_base,
        input_artifacts=[candidate_set_report, candidate_rows, protocol_v2_template_path],
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "source_task_id": report["source_task_id"],
            "pair_id": report["pair_id"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "control_runtime_config": str(result["control_config_path"]),
            "treatment_runtime_config": str(result["treatment_config_path"]),
            "mapping": str(result["mapping_path"]),
            "replay_action_count": summary["replay_action_count"],
            "mapping_count": summary["mapping_count"],
            "allow_protocol_v2_live_single_execute": policy[
                "allow_protocol_v2_live_single_execute"
            ],
            "allow_protocol_v2_real_run_without_explicit_ack": policy[
                "allow_protocol_v2_real_run_without_explicit_ack"
            ],
            "recommended_next_step": policy["recommended_next_step"],
        }
    )


@app.command("route-b1-plan")
def route_b1_plan(
    prereg_manifest: Path = typer.Argument(..., help="Route B probe prereg manifest JSON (task18)."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="B1 plan evidence directory."),
) -> None:
    """Offline: build the Protocol B1 + per-anchor arm plan. No Docker/provider/eval."""
    result = write_route_b1_plan_evidence(
        prereg_manifest=json.loads(prereg_manifest.read_text()),
        output_dir=output_dir,
        input_artifacts=[prereg_manifest],
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "protocol_hash": report["protocol_hash"],
            "summary": report["summary"],
            "blocking_failures": report["blocking_failures"],
            "protocol": str(result["protocol_path"]),
            "anchor_plan": str(result["anchors_path"]),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("route-b1-antileak")
def route_b1_antileak(
    plan_dir: Path = typer.Argument(..., help="Directory produced by route-b1-plan."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Anti-leak evidence directory."),
    gold_jsonl: Optional[Path] = typer.Option(None, "--gold-jsonl", help="Gold rows (for live content-diff readiness)."),
) -> None:
    """Offline: M2 contract-level anti-oracle-leakage preflight. Content diff is a live-time gate."""
    gold_task_ids: list[str] = []
    if gold_jsonl is not None and gold_jsonl.is_file():
        for row in read_jsonl(gold_jsonl):
            task_id = row.get("instance_id") or row.get("source_task_id") or row.get("task_id")
            if task_id:
                gold_task_ids.append(str(task_id))
    result = write_route_b1_antileak_evidence(
        plan_dir=plan_dir,
        output_dir=output_dir,
        gold_task_ids=gold_task_ids,
        input_artifacts=[gold_jsonl] if gold_jsonl is not None else None,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "protocol_hash": report["protocol_hash"],
            "gold_available_for_live_content_diff": report["gold_available_for_live_content_diff"],
            "content_diff_stage": report["content_diff_stage"],
            "blocking_failures": report["blocking_failures"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("route-b1-eligibility-screen")
def route_b1_eligibility_screen(
    problem_statements: Path = typer.Argument(..., help="JSONL of {instance_id, problem_statement}."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Eligibility evidence directory."),
) -> None:
    """Offline (Amendment A §4): screen each anchor's issue text for an actionable,
    issue-only reproduction. Anchors without one are ineligible for the main probe."""
    rows = list(read_jsonl(problem_statements))
    results = [
        issue_repro_eligibility(
            str(r.get("instance_id") or r.get("source_task_id") or ""), r.get("problem_statement")
        )
        for r in rows
    ]
    eligible = [r.instance_id for r in results if r.eligible]
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "phase": "route_b.b1_eligibility_screen",
        "decision": "route_b1_eligibility_screen_complete",
        "screened": len(results),
        "eligible_count": len(eligible),
        "eligible_anchors": eligible,
        "rows": [
            {"instance_id": r.instance_id, "eligible": r.eligible, "markers": list(r.markers), "reason": r.reason}
            for r in results
        ],
        "claim_boundary": "issue-text-only repro eligibility; ineligible anchors must be excluded or demoted (no oracle-leaking repro to pad the set).",
    }
    out = output_dir / "b1_eligibility_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _emit_json({"decision": report["decision"], "screened": report["screened"], "eligible": eligible, "report": str(out)})


@app.command("route-b1-live-arm")
def route_b1_live_arm(
    config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    arm_type: str = typer.Option("treatment", "--arm", help="control or treatment."),
    protocol_path: Optional[Path] = typer.Option(None, "--protocol", help="Protocol B1 JSON (default: template)."),
    source_task_id: Optional[str] = typer.Option(None, "--source-task-id"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
    problem_statement_file: Optional[Path] = typer.Option(
        None, "--problem-statement-file", help="Issue text for the treatment payload (issue-text-only)."
    ),
    gold_jsonl: Optional[Path] = typer.Option(
        None, "--gold-jsonl", help="Gold rows; used ONLY as M2b leak refs (FAIL_TO_PASS/test_patch/patch). Never injected."
    ),
    execute: bool = typer.Option(False, "--execute", help="Run the guarded live-single arm."),
    ack_docker: bool = typer.Option(False, "--ack-docker"),
    ack_external_provider: bool = typer.Option(False, "--ack-external-provider"),
    ack_official_eval: bool = typer.Option(False, "--ack-official-eval"),
) -> None:
    """Plan (default) or execute one Route B1 live arm. Replay-free; treatment injects
    the issue-text reproduction once. M2b leak-scan blocks any FAIL_TO_PASS/test_patch/gold overlap."""
    if arm_type not in {"control", "treatment"}:
        raise typer.BadParameter("--arm must be control or treatment")
    protocol = (
        ProtocolB1.from_dict(json.loads(protocol_path.read_text())) if protocol_path else protocol_b1_template()
    )
    payload = None
    leak = B1LeakRefs()
    if arm_type == "treatment":
        issue_text = problem_statement_file.read_text() if problem_statement_file else None
        payload = build_b1_payload(
            instance_id=source_task_id or "", problem_statement=issue_text, repro_traceback=None
        )
        if gold_jsonl is not None and gold_jsonl.is_file():
            for r in read_jsonl(gold_jsonl):
                if str(r.get("instance_id") or "") == (source_task_id or ""):
                    ftp = r.get("FAIL_TO_PASS") or r.get("fail_to_pass") or []
                    if isinstance(ftp, str):
                        try:
                            ftp = json.loads(ftp)
                        except Exception:
                            ftp = [ftp]
                    leak = B1LeakRefs(
                        fail_to_pass=[str(x) for x in ftp],
                        test_patch=r.get("test_patch"),
                        gold_patch=r.get("patch") or r.get("gold_patch"),
                    )
                    break
    result = run_sweagent_b1_live_single(
        spec=SWEAgentB1LiveSingleSpec(
            config_path=config,
            output_dir=output_dir,
            protocol=protocol,
            arm_type=arm_type,  # type: ignore[arg-type]
            payload=payload,
            leak_refs=leak,
            execute=execute,
            source_task_id=source_task_id,
            pair_id=pair_id,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "arm_type": report["arm_type"],
            "m2b_leak_findings": report["m2b_leak_findings"],
            "injection_count": report["injection_count"],
            "blocking_failures": report["blocking_failures"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("route-b1-cells")
def route_b1_cells_cmd(
    arms_root: Path = typer.Argument(..., help="Root dir holding b1_live_arm_report.json files (arms/<anchor>/<arm>/rep_<n>/)."),
    resolved_labels: Path = typer.Option(..., "--resolved-labels", help="JSONL {anchor,arm,rep,resolved} from official SWE-bench eval (STEP 6)."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Cells evidence directory."),
) -> None:
    """Offline: join each live-arm report's M-check provenance with its official-eval
    resolved label into the per-cell JSONL that route-b1-decision consumes."""
    arm_reports = discover_arm_reports(arms_root)
    resolved = resolved_map_from_labels(list(read_jsonl(resolved_labels)))
    result = assemble_cells(arm_reports, resolved)
    output_dir.mkdir(parents=True, exist_ok=True)
    cells_path = output_dir / "b1_cells.jsonl"
    write_jsonl(cells_path, result["cells"])
    (output_dir / "b1_cells_report.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "cells"}, ensure_ascii=False, indent=2) + "\n"
    )
    _emit_json(
        {
            "cell_count": result["cell_count"],
            "incomplete_count": result["incomplete_count"],
            "complete": result["complete"],
            "cells": str(cells_path),
            "next_step": "wutai-clinic route-b1-decision " + str(cells_path) + " -o <decision_dir>",
        }
    )


@app.command("route-b1-decision")
def route_b1_decision_cmd(
    cells: Path = typer.Argument(..., help="Per-cell outcomes JSONL {anchor,arm,resolved,injected_once,leak_clean,trigger_hit,injection_count}."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Decision evidence directory."),
) -> None:
    """Offline: apply the frozen §5 preregistered decision over completed B1 cells.
    Emits signal_of_life | futility_null | inconclusive_recalibrate. No uplift claim."""
    rows = list(read_jsonl(cells))
    report = route_b1_decision(aggregate_cells_to_anchor_outcomes(rows))
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "b1_decision_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "counted_anchors": report["counted_anchor_count"],
            "signal_anchors": report["signal_anchors"],
            "no_uplift_anchors": report["no_uplift_anchor_count"],
            "next_step": report["next_step"],
            "report": str(out),
        }
    )


@app.command("sweagent-protocol-v1-live-single")
def sweagent_protocol_v1_live_single(
    config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    protocol: Path = typer.Option(..., "--protocol", help="Protocol v1 JSON file."),
    replay_actions: Optional[Path] = typer.Option(
        None,
        "--replay-actions",
        help="Replay actions JSON/YAML list. Required for --execute.",
    ),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    arm_type: str = typer.Option("treatment", "--arm", help="control or treatment."),
    source_task_id: Optional[str] = typer.Option(None, "--source-task-id"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
    execute: bool = typer.Option(False, "--execute", help="Run the guarded live-single arm."),
    ack_docker: bool = typer.Option(
        False, "--ack-docker", help="Acknowledge that execute mode may start Docker."
    ),
    ack_external_provider: bool = typer.Option(
        False,
        "--ack-external-provider",
        help="Acknowledge that execute mode may call a model provider.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge official-eval import if required later.",
    ),
    require_official_eval: bool = typer.Option(
        False,
        "--require-official-eval",
        help="Require official-eval acknowledgement in the execution gate.",
    ),
) -> None:
    """Plan or execute one Protocol v1 SWE-agent live-single arm."""
    if arm_type not in {"control", "treatment"}:
        raise typer.BadParameter("--arm must be control or treatment")
    result = run_sweagent_protocol_v1_live_single(
        spec=SWEAgentProtocolV1LiveSingleSpec(
            config_path=config,
            output_dir=output_dir,
            protocol=ProtocolV1.from_dict(json.loads(protocol.read_text())),
            replay_actions=load_replay_actions(replay_actions) if replay_actions else [],
            arm_type=arm_type,  # type: ignore[arg-type]
            execute=execute,
            source_task_id=source_task_id,
            pair_id=pair_id,
            require_official_eval=require_official_eval,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "arm_type": report["arm_type"],
            "execute_requested": report["execute_requested"],
            "run_single_started": report["run_single_started"],
            "constraint_blocked": report["constraint_blocked"],
            "hook_event_count": report["hook_event_count"],
            "model_event_count": report["model_event_count"],
            "replay_action_count": report["replay_action_count"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "replay_actions": str(result["replay_path"]),
        }
    )


@app.command("sweagent-protocol-v1-activate-runtime-config")
def sweagent_protocol_v1_activate_runtime_config(
    config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    arm_type: str = typer.Option(..., "--arm", help="control or treatment."),
    native_output_dir: Optional[Path] = typer.Option(
        None,
        "--native-output-dir",
        help="Arm-specific SWE-agent native output directory.",
    ),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Override agent.model.name."),
    api_base: Optional[str] = typer.Option(
        None,
        "--api-base",
        help="Optional non-secret provider base URL to write into agent.model.api_base.",
    ),
    per_instance_call_limit: int = typer.Option(
        20,
        "--per-instance-call-limit",
        help="Hard API call cap for this arm.",
    ),
    per_instance_cost_limit: float = typer.Option(
        0.0,
        "--per-instance-cost-limit",
        help="SWE-agent per-instance cost limit. Keep 0 for unknown proxy models.",
    ),
    total_cost_limit: float = typer.Option(
        0.0,
        "--total-cost-limit",
        help="SWE-agent total cost limit. Keep 0 for unknown proxy models.",
    ),
    provider_key_env: str = typer.Option("OPENAI_API_KEY", "--provider-key-env"),
    provider_api_base_env: str = typer.Option("OPENAI_API_BASE", "--provider-api-base-env"),
    source_task_id: Optional[str] = typer.Option(None, "--source-task-id"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
) -> None:
    """Create a secret-free executable RunSingle config for one Protocol v1 arm."""
    if arm_type not in {"control", "treatment"}:
        raise typer.BadParameter("--arm must be control or treatment")
    result = activate_sweagent_protocol_v1_runtime_config(
        SWEAgentProtocolV1RuntimeConfigSpec(
            config_path=config,
            output_dir=output_dir,
            arm_type=arm_type,  # type: ignore[arg-type]
            native_output_dir=native_output_dir,
            model_name=model_name,
            api_base=api_base,
            per_instance_call_limit=per_instance_call_limit,
            per_instance_cost_limit=per_instance_cost_limit,
            total_cost_limit=total_cost_limit,
            provider_key_env=provider_key_env,
            provider_api_base_env=provider_api_base_env,
            source_task_id=source_task_id,
            pair_id=pair_id,
        )
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "arm_type": report["arm_type"],
            "model_name": report["model_name"],
            "per_instance_call_limit": report["per_instance_call_limit"],
            "per_instance_cost_limit": report["per_instance_cost_limit"],
            "total_cost_limit": report["total_cost_limit"],
            "api_base_configured": report["api_base_configured"],
            "activated_config": str(result["config_path"]),
            "native_output_dir": report["native_output_dir"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("sweagent-protocol-v1-live-pair")
def sweagent_protocol_v1_live_pair(
    control_dir: Path = typer.Argument(..., help="Completed control Protocol v1 live-single dir."),
    treatment_dir: Path = typer.Argument(
        ..., help="Completed treatment Protocol v1 live-single dir."
    ),
    control_patch: Path = typer.Option(..., "--control-patch", help="Control patch file."),
    treatment_patch: Path = typer.Option(..., "--treatment-patch", help="Treatment patch file."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Pair evidence directory."),
    control_resolved: Optional[str] = typer.Option(
        None, "--control-resolved", help="Optional control outcome."
    ),
    treatment_resolved: Optional[str] = typer.Option(
        None, "--treatment-resolved", help="Optional treatment outcome."
    ),
    outcome_source: str = typer.Option(
        "not_provided",
        "--outcome-source",
        help="not_provided, operator_supplied, or official_eval.",
    ),
    ack_official_eval: bool = typer.Option(False, "--ack-official-eval"),
) -> None:
    """Combine two completed Protocol v1 live-single arms into a pair-level handoff."""
    if outcome_source not in {"not_provided", "operator_supplied", "official_eval"}:
        raise typer.BadParameter("--outcome-source must be not_provided, operator_supplied, or official_eval")
    result = run_sweagent_protocol_v1_live_pair(
        spec=SWEAgentProtocolV1LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=output_dir,
            control_patch=control_patch,
            treatment_patch=treatment_patch,
            control_resolved=_parse_optional_bool(control_resolved, name="--control-resolved"),
            treatment_resolved=_parse_optional_bool(
                treatment_resolved,
                name="--treatment-resolved",
            ),
            outcome_source=outcome_source,  # type: ignore[arg-type]
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["summary_path"]),
        }
    )


@app.command("sweagent-protocol-v1-official-eval")
def sweagent_protocol_v1_official_eval(
    pair_dir: Path = typer.Argument(..., help="Protocol v1 live-pair evidence directory."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Official-eval output dir."),
    eval_dir: Optional[Path] = typer.Option(
        None,
        "--eval-dir",
        help="Isolated official SWE-bench eval directory.",
    ),
    run_official_eval: bool = typer.Option(
        False,
        "--run-official-eval",
        help="Invoke the official SWE-bench harness.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that official eval may run/import outcomes.",
    ),
    run_id: str = typer.Option("phase6_protocol_v1_official_eval", "--run-id"),
    dataset_name: str = typer.Option("SWE-bench/SWE-bench_Lite", "--dataset-name"),
    split: str = typer.Option("test", "--split"),
    max_workers: int = typer.Option(1, "--max-workers"),
    timeout: int = typer.Option(1800, "--timeout"),
    build_compat: Optional[str] = typer.Option("legacy-python-packaging", "--build-compat"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
) -> None:
    """Write predictions and optionally run official eval for one Protocol v1 pair."""
    result = run_sweagent_protocol_v1_official_eval(
        spec=SWEAgentProtocolV1OfficialEvalSpec(
            pair_dir=pair_dir,
            output_dir=output_dir,
            eval_dir=eval_dir,
            run_official_eval=run_official_eval,
            run_id=run_id,
            dataset_name=dataset_name,
            split=split,
            max_workers=max_workers,
            timeout=timeout,
            build_compat=build_compat,
            pair_id=pair_id,
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "official_eval_started": report["official_eval_started"],
            "official_eval_completed": report["official_eval_completed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["pair_summary_path"]),
            "dual_scorecard": str(result["dual_scorecard_path"]),
        }
    )


@app.command("sweagent-protocol-v2-live-single")
def sweagent_protocol_v2_live_single(
    config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    protocol: Path = typer.Option(..., "--protocol", help="Protocol v2 JSON file."),
    replay_actions: Optional[Path] = typer.Option(
        None,
        "--replay-actions",
        help="Replay actions JSON/YAML list. Required for --execute.",
    ),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    arm_type: str = typer.Option("treatment", "--arm", help="control or treatment."),
    source_task_id: Optional[str] = typer.Option(None, "--source-task-id"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
    execute: bool = typer.Option(False, "--execute", help="Run the guarded live-single arm."),
    allow_empty_replay: bool = typer.Option(
        False,
        "--allow-empty-replay",
        help="Explicitly authorize a fully-live run with no replay prefix (task10 probe).",
    ),
    observe_only: bool = typer.Option(
        False,
        "--observe-only",
        help="Prescription v3: constraint hook detects violations but never enforces.",
    ),
    ack_docker: bool = typer.Option(
        False, "--ack-docker", help="Acknowledge that execute mode may start Docker."
    ),
    ack_external_provider: bool = typer.Option(
        False,
        "--ack-external-provider",
        help="Acknowledge that execute mode may call a model provider.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge official-eval import if required later.",
    ),
    require_official_eval: bool = typer.Option(
        False,
        "--require-official-eval",
        help="Require official-eval acknowledgement in the execution gate.",
    ),
) -> None:
    """Plan or execute one Protocol v2 SWE-agent live-single arm."""
    if arm_type not in {"control", "treatment"}:
        raise typer.BadParameter("--arm must be control or treatment")
    payload = json.loads(protocol.read_text())
    protocol_payload = payload.get("protocol_v2") if isinstance(payload, dict) else None
    result = run_sweagent_protocol_v2_live_single(
        spec=SWEAgentProtocolV2LiveSingleSpec(
            config_path=config,
            output_dir=output_dir,
            protocol=ProtocolV2.from_dict(protocol_payload or payload),
            replay_actions=load_replay_actions(replay_actions) if replay_actions else [],
            arm_type=arm_type,  # type: ignore[arg-type]
            execute=execute,
            source_task_id=source_task_id,
            pair_id=pair_id,
            require_official_eval=require_official_eval,
            allow_empty_replay=allow_empty_replay,
            observe_only=observe_only,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "arm_type": report["arm_type"],
            "execute_requested": report["execute_requested"],
            "run_single_started": report["run_single_started"],
            "constraint_blocked": report["constraint_blocked"],
            "hook_event_count": report["hook_event_count"],
            "model_event_count": report["model_event_count"],
            "replay_action_count": report["replay_action_count"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "replay_actions": str(result["replay_path"]),
        }
    )


@app.command("sweagent-protocol-v2-live-pair")
def sweagent_protocol_v2_live_pair(
    control_dir: Path = typer.Argument(..., help="Completed control Protocol v2 live-single dir."),
    treatment_dir: Path = typer.Argument(
        ..., help="Completed treatment Protocol v2 live-single dir."
    ),
    control_patch: Path = typer.Option(..., "--control-patch", help="Control patch file."),
    treatment_patch: Path = typer.Option(..., "--treatment-patch", help="Treatment patch file."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Pair evidence directory."),
    control_resolved: Optional[str] = typer.Option(
        None, "--control-resolved", help="Optional control outcome."
    ),
    treatment_resolved: Optional[str] = typer.Option(
        None, "--treatment-resolved", help="Optional treatment outcome."
    ),
    outcome_source: str = typer.Option(
        "not_provided",
        "--outcome-source",
        help="not_provided, operator_supplied, or official_eval.",
    ),
    ack_official_eval: bool = typer.Option(False, "--ack-official-eval"),
) -> None:
    """Combine two completed Protocol v2 live-single arms into a pair-level handoff."""
    if outcome_source not in {"not_provided", "operator_supplied", "official_eval"}:
        raise typer.BadParameter("--outcome-source must be not_provided, operator_supplied, or official_eval")
    result = run_sweagent_protocol_v2_live_pair(
        spec=SWEAgentProtocolV2LivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=output_dir,
            control_patch=control_patch,
            treatment_patch=treatment_patch,
            control_resolved=_parse_optional_bool(control_resolved, name="--control-resolved"),
            treatment_resolved=_parse_optional_bool(
                treatment_resolved,
                name="--treatment-resolved",
            ),
            outcome_source=outcome_source,  # type: ignore[arg-type]
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["summary_path"]),
        }
    )


@app.command("sweagent-protocol-v2-official-eval")
def sweagent_protocol_v2_official_eval(
    pair_dir: Path = typer.Argument(..., help="Protocol v2 live-pair evidence directory."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Official-eval output dir."),
    eval_dir: Optional[Path] = typer.Option(
        None,
        "--eval-dir",
        help="Isolated official SWE-bench eval directory.",
    ),
    run_official_eval: bool = typer.Option(
        False,
        "--run-official-eval",
        help="Invoke the official SWE-bench harness.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that official eval may run/import outcomes.",
    ),
    run_id: str = typer.Option("phase6_protocol_v2_official_eval", "--run-id"),
    dataset_name: str = typer.Option("SWE-bench/SWE-bench_Lite", "--dataset-name"),
    split: str = typer.Option("test", "--split"),
    max_workers: int = typer.Option(1, "--max-workers"),
    timeout: int = typer.Option(1800, "--timeout"),
    build_compat: Optional[str] = typer.Option("legacy-python-packaging", "--build-compat"),
    pair_id: Optional[str] = typer.Option(None, "--pair-id"),
) -> None:
    """Write predictions and optionally run official eval for one Protocol v2 pair."""
    result = run_sweagent_protocol_v2_official_eval(
        spec=SWEAgentProtocolV2OfficialEvalSpec(
            pair_dir=pair_dir,
            output_dir=output_dir,
            eval_dir=eval_dir,
            run_official_eval=run_official_eval,
            run_id=run_id,
            dataset_name=dataset_name,
            split=split,
            max_workers=max_workers,
            timeout=timeout,
            build_compat=build_compat,
            pair_id=pair_id,
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "official_eval_started": report["official_eval_started"],
            "official_eval_completed": report["official_eval_completed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["pair_summary_path"]),
            "dual_scorecard": str(result["dual_scorecard_path"]),
        }
    )


@app.command("sweagent-preflight")
def sweagent_preflight(
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", help="Evidence package output directory."
    ),
    control_resolved: Optional[str] = typer.Option(
        None, "--control-resolved", help="Optional mock control outcome."
    ),
    treatment_resolved: Optional[str] = typer.Option(
        None, "--treatment-resolved", help="Optional mock treatment outcome."
    ),
    mismatch_model_request: bool = typer.Option(
        False,
        "--mismatch-model-request",
        help="Intentionally change the treatment model_request_hash to verify blocking.",
    ),
) -> None:
    """Generate the SWE-agent adapter preflight evidence package."""
    result = run_sweagent_fork_preflight(
        output_dir=output_dir,
        control_resolved=_parse_optional_bool(control_resolved, name="--control-resolved"),
        treatment_resolved=_parse_optional_bool(
            treatment_resolved,
            name="--treatment-resolved",
        ),
        treatment_capsule_overrides={"model_request_hash": "intentional_mismatch"}
        if mismatch_model_request
        else None,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
        }
    )


@app.command("sweagent-live-plan")
def sweagent_live_plan(
    ack_docker: bool = typer.Option(
        False, "--ack-docker", help="Acknowledge that the next live run may start Docker."
    ),
    ack_external_provider: bool = typer.Option(
        False,
        "--ack-external-provider",
        help="Acknowledge that the next live run may call a model provider.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that the next live run may use official evaluation.",
    ),
    require_official_eval: bool = typer.Option(
        False,
        "--require-official-eval",
        help="Require official-eval acknowledgement in the readiness gate.",
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON report path."),
) -> None:
    """Check authorization gates for a future SWE-agent RunSingle live attachment."""
    report = sweagent_live_plan_report(
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
        require_official_eval=require_official_eval,
    )
    if output is not None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        _emit_json(report)


@app.command("sweagent-live-single")
def sweagent_live_single(
    config: Path = typer.Argument(..., help="SWE-agent RunSingle JSON/YAML config."),
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", help="Evidence package output directory."
    ),
    arm_type: str = typer.Option("control", "--arm", help="control or treatment."),
    protocol: Optional[Path] = typer.Option(None, "--protocol", help="Protocol v0 JSON/YAML."),
    replay_actions: Optional[Path] = typer.Option(
        None, "--replay-actions", help="Replay actions JSON/YAML list."
    ),
    features: Optional[Path] = typer.Option(None, "--features", help="Live feature JSON/YAML mapping."),
    reference_capsule: Optional[Path] = typer.Option(
        None, "--reference-capsule", help="Control State Capsule JSON/YAML for treatment runs."
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Actually construct RunSingle, attach the adapter, and call run()."
    ),
    ack_docker: bool = typer.Option(
        False, "--ack-docker", help="Acknowledge that execute mode may start Docker."
    ),
    ack_external_provider: bool = typer.Option(
        False,
        "--ack-external-provider",
        help="Acknowledge that execute mode may call a model provider.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that execute mode may use official evaluation.",
    ),
    require_official_eval: bool = typer.Option(
        False,
        "--require-official-eval",
        help="Require official-eval acknowledgement in the execution gate.",
    ),
) -> None:
    """Plan or execute one guarded SWE-agent RunSingle arm."""
    if arm_type not in {"control", "treatment"}:
        raise typer.BadParameter("--arm must be control or treatment")
    protocol_payload = InterventionProtocol.from_file(protocol) if protocol else default_protocol()
    reference_payload = StateCapsule.from_file(reference_capsule) if reference_capsule else None
    result = run_sweagent_live_single(
        spec=SWEAgentLiveSingleSpec(
            config_path=config,
            output_dir=output_dir,
            arm_type=arm_type,  # type: ignore[arg-type]
            execute=execute,
            protocol=protocol_payload,
            replay_actions=load_replay_actions(replay_actions),
            features=load_features(features),
            reference_capsule=reference_payload,
            require_official_eval=require_official_eval,
        ),
        policy=RuntimePermissionPolicy(
            allow_docker=ack_docker,
            allow_external_provider=ack_external_provider,
            allow_official_eval=ack_official_eval,
        ),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "arm_type": report["arm_type"],
            "execute_requested": report["execute_requested"],
            "run_single_started": report["run_single_started"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "events": str(result["events_path"]),
            "capsule": str(result["capsule_path"]) if result["capsule_path"] else None,
        }
    )


@app.command("sweagent-live-pair")
def sweagent_live_pair(
    control_dir: Path = typer.Argument(..., help="Completed control sweagent-live-single dir."),
    treatment_dir: Path = typer.Argument(..., help="Completed treatment sweagent-live-single dir."),
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", help="Pair-level evidence package output directory."
    ),
    control_resolved: Optional[str] = typer.Option(
        None, "--control-resolved", help="Optional control outcome: true/false or resolved/unresolved."
    ),
    treatment_resolved: Optional[str] = typer.Option(
        None,
        "--treatment-resolved",
        help="Optional treatment outcome: true/false or resolved/unresolved.",
    ),
    outcome_source: str = typer.Option(
        "not_provided",
        "--outcome-source",
        help="not_provided, operator_supplied, or official_eval.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that supplied outcomes came from official evaluation.",
    ),
) -> None:
    """Combine two completed SWE-agent live arms into a pair-level outcome audit."""
    if outcome_source not in {"not_provided", "operator_supplied", "official_eval"}:
        raise typer.BadParameter("--outcome-source must be not_provided, operator_supplied, or official_eval")
    result = run_sweagent_live_pair(
        spec=SWEAgentLivePairSpec(
            control_dir=control_dir,
            treatment_dir=treatment_dir,
            output_dir=output_dir,
            control_resolved=_parse_optional_bool(control_resolved, name="--control-resolved"),
            treatment_resolved=_parse_optional_bool(
                treatment_resolved,
                name="--treatment-resolved",
            ),
            outcome_source=outcome_source,  # type: ignore[arg-type]
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["summary_path"]),
        }
    )


@app.command("sweagent-official-pair")
def sweagent_official_pair(
    pair_summary: Path = typer.Argument(..., help="Official eval pair summary JSONL."),
    official_eval_report: Path = typer.Argument(..., help="Official eval aggregate report JSON."),
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", help="Official pair evidence package output directory."
    ),
    pair_id: Optional[str] = typer.Option(None, "--pair-id", help="Pair id to import."),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that this command imports official resolved/unresolved outcomes.",
    ),
    allow_secondary: bool = typer.Option(
        False,
        "--allow-secondary",
        help="Allow importing a secondary audit row instead of a main attribution row.",
    ),
) -> None:
    """Import one completed SWE-bench official-eval pair as an auditable outcome package."""
    result = run_sweagent_official_pair(
        spec=SWEAgentOfficialPairSpec(
            pair_summary_path=pair_summary,
            official_eval_report=official_eval_report,
            output_dir=output_dir,
            pair_id=pair_id,
            require_main_attribution=not allow_secondary,
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "pair_id": report["pair_id"],
            "source_task_id": report["source_task_id"],
            "control_resolved": report["control_resolved"],
            "treatment_resolved": report["treatment_resolved"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["summary_path"]),
        }
    )


@app.command("phase6-official-eval")
def phase6_official_eval(
    live_preflight_dir: Path = typer.Argument(
        ..., help="Phase 5 live-hook preflight evidence directory."
    ),
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", help="Phase 6 official-eval evidence package output directory."
    ),
    eval_dir: Optional[Path] = typer.Option(
        None,
        "--eval-dir",
        help="Separate SWE-bench official eval output directory. Defaults under output-dir.",
    ),
    run_id: str = typer.Option(
        "phase6_sweagent_live_pair_official_eval",
        "--run-id",
        help="SWE-bench official eval run id.",
    ),
    dataset_name: str = typer.Option(
        "SWE-bench/SWE-bench_Lite",
        "--dataset-name",
        help="SWE-bench dataset name.",
    ),
    split: str = typer.Option("test", "--split", help="SWE-bench split."),
    max_workers: int = typer.Option(1, "--max-workers", help="Official eval workers."),
    timeout: int = typer.Option(1800, "--timeout", help="Official eval timeout in seconds."),
    build_compat: Optional[str] = typer.Option(
        "legacy-python-packaging",
        "--build-compat",
        help="Optional build compatibility mode. Use 'none' to call swebench directly.",
    ),
    run_official_eval: bool = typer.Option(
        False,
        "--run-official-eval",
        help="Actually invoke the official SWE-bench harness for both arms.",
    ),
    ack_official_eval: bool = typer.Option(
        False,
        "--ack-official-eval",
        help="Acknowledge that this may run or import official SWE-bench outcomes.",
    ),
    pair_id: Optional[str] = typer.Option(None, "--pair-id", help="Override pair id."),
) -> None:
    """Build a Phase 6 outcome-backed official-eval package from one live pair."""
    normalized_build_compat = None if build_compat in {None, "none"} else build_compat
    result = run_sweagent_phase6_official_eval(
        spec=SWEAgentPhase6OfficialEvalSpec(
            live_preflight_dir=live_preflight_dir,
            output_dir=output_dir,
            eval_dir=eval_dir,
            run_official_eval=run_official_eval,
            run_id=run_id,
            dataset_name=dataset_name,
            split=split,
            max_workers=max_workers,
            timeout=timeout,
            build_compat=normalized_build_compat,
            pair_id=pair_id,
        ),
        policy=RuntimePermissionPolicy(allow_official_eval=ack_official_eval),
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "effect_label": report["effect_label"],
            "official_eval_completed": report["official_eval_completed"],
            "pair_id": report["pair_id"],
            "source_task_id": report["source_task_id"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_summary": str(result["pair_summary_path"]),
            "dual_scorecard": str(result["dual_scorecard_path"]),
        }
    )


@app.command()
def audit(
    evidence_dir: Path = typer.Argument(
        ..., help="Directory containing report/manifest artifacts."
    ),
    from_phase: Optional[str] = typer.Option(None, help="Start phase prefix."),
    to_phase: Optional[str] = typer.Option(None, help="End phase prefix."),
) -> None:
    """Scan legacy reports and emit a compact evidence inventory."""
    reports = []
    for path in sorted(evidence_dir.glob("*_report.json")):
        name = path.name
        if from_phase and name < from_phase:
            continue
        if to_phase and name > to_phase:
            continue
        data = json.loads(path.read_text())
        if not any(key in data for key in ("phase", "decision", "gates")):
            continue
        report = Report.from_legacy(data)
        reports.append(
            {
                "path": path.name,
                "phase": report.phase,
                "decision": report.decision,
                "gate_count": len(report.gates),
                "passed": report.passed,
                "claim_boundary_present": report.claim_boundary is not None,
            }
        )
    manifests = []
    hash_audits = []
    for path in sorted(evidence_dir.glob("*_manifest.json")):
        data = json.loads(path.read_text())
        hash_audit = _audit_manifest_hashes(path, evidence_dir, data)
        hash_audits.append(hash_audit)
        manifests.append(
            {
                "path": path.name,
                "phase": data.get("phase"),
                "decision": data.get("decision"),
                "passed": data.get("passed"),
                "hash_checked": hash_audit["hash_checked"],
                "hash_missing_count": hash_audit["hash_missing_count"],
                "hash_mismatch_count": hash_audit["hash_mismatch_count"],
                "record_count_mismatch_count": hash_audit["record_count_mismatch_count"],
            }
        )
    hash_checked = sum(int(item["hash_checked"]) for item in hash_audits)
    hash_missing = sum(int(item["hash_missing_count"]) for item in hash_audits)
    hash_mismatches = sum(int(item["hash_mismatch_count"]) for item in hash_audits)
    record_mismatches = sum(int(item["record_count_mismatch_count"]) for item in hash_audits)
    _emit_json(
        {
            "report_count": len(reports),
            "manifest_count": len(manifests),
            "hash_checked": hash_checked,
            "hash_missing_count": hash_missing,
            "hash_mismatch_count": hash_mismatches,
            "record_count_mismatch_count": record_mismatches,
            "hash_consistency_passed": (
                hash_checked > 0
                and hash_missing == 0
                and hash_mismatches == 0
                and record_mismatches == 0
            ),
            "reports": reports,
            "manifests": manifests,
        }
    )


@app.command("evidence-index")
def evidence_index_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory to scan."),
    output_dir: Path = typer.Option(..., "-o", help="Output directory for index artifacts."),
    table: bool = typer.Option(False, "--table", help="Emit human-readable summary table."),
) -> None:
    """Build a machine-readable index of evidence artifacts."""
    result = write_evidence_index(evidence_root, output_dir)
    if table:
        summary = result["summary"]
        typer.echo(f"total_rows:          {summary['total_rows']}")
        typer.echo(f"stratum_counts:      {summary['stratum_counts']}")
        typer.echo(f"label_counts:        {summary['label_counts']}")
        typer.echo(f"uplift_pair_count:   {summary['uplift_pair_count']}")
        typer.echo(f"harm_pair_count:     {summary['harm_pair_count']}")
        typer.echo(f"materialized_not_executed_count: {summary['materialized_not_executed_count']}")
        typer.echo(f"unparsed_count:      {summary['unparsed_count']}")
        typer.echo(f"status_counts:       {summary['status_counts']}")
        typer.echo(f"rows_path:           {result['rows_path']}")
        typer.echo(f"report_path:         {result['report_path']}")
        return
    _emit_json(
        {
            "decision": result["report"]["decision"],
            "output_dir": str(output_dir),
            "rows_path": str(result["rows_path"]),
            "report_path": str(result["report_path"]),
            "manifest_path": str(result["manifest_path"]),
            "summary": result["summary"],
        }
    )


@app.command("power-analysis")
def power_analysis_cmd(
    pairs: int = typer.Option(..., "--pairs", help="Total effective pairs completed"),
    uplift: int = typer.Option(0, "--uplift", help="Number of uplift pairs"),
    harm: int = typer.Option(0, "--harm", help="Number of harm pairs"),
    trigger_hit_rate: float = typer.Option(0.6, "--trigger-hit-rate"),
    target_uplift_rate: float = typer.Option(0.3, "--target-uplift-rate"),
    batch_outcomes_report: Optional[Path] = typer.Option(None, "--batch-outcomes-report"),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Quantify sample-size requirements and exclusion bounds for paired outcomes."""
    result = write_power_report(
        output_dir,
        n_pairs=pairs,
        n_uplift=uplift,
        n_harm=harm,
        trigger_hit_rate=trigger_hit_rate,
        target_uplift_rate=target_uplift_rate,
        batch_outcomes_report=batch_outcomes_report,
    )
    report = result["report"]
    summary = report["summary"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "minimum_pairs_for_powered_claim": summary["minimum_pairs_for_powered_claim"],
            "max_effect_excluded_95": summary["max_effect_excluded_95"],
            "futility_status": summary["futility_status"],
            "powered_for_target_effect": summary["powered_for_target_effect"],
        }
    )


@app.command("protocol-v2-batch-outcomes")
def protocol_v2_batch_outcomes_cmd(
    evidence_root: Path = typer.Argument(..., help="Phase 6 Protocol v2 evidence root."),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Evidence package directory."),
    target_pair_count: int = typer.Option(
        4,
        "--target-pair-count",
        help="Strict-fresh Protocol v2 pair count required before a stability claim.",
    ),
    include_v1_reference: bool = typer.Option(
        True,
        "--include-v1-reference/--no-v1-reference",
        help="Include Protocol v1 pairs as separately stratified reference context.",
    ),
    include_v0_reference: bool = typer.Option(
        True,
        "--include-v0-reference/--no-v0-reference",
        help="Include v0 state-capsule pairs as separately stratified reference context.",
    ),
) -> None:
    """Aggregate Protocol v2 official outcomes without mixing v1/v0 reference evidence."""
    result = write_protocol_v2_batch_outcomes_evidence(
        root=evidence_root,
        output_dir=output_dir,
        include_v1_reference=include_v1_reference,
        include_v0_reference=include_v0_reference,
        target_pair_count=target_pair_count,
    )
    report = result["report"]
    summary = report["summary"]
    policy = report["continuation_policy"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "strict_fresh_pair_count": summary["strict_fresh_pair_count"],
            "reference_pair_count": summary["reference_pair_count"],
            "v1_reference_pair_count": summary["v1_reference_pair_count"],
            "v0_reference_pair_count": summary["v0_reference_pair_count"],
            "uplift_pair_count": summary["uplift_pair_count"],
            "harm_pair_count": summary["harm_pair_count"],
            "fresh_list_degraded": summary["fresh_list_degraded"],
            "allow_continue_remaining_fresh_targets": policy[
                "allow_continue_remaining_fresh_targets"
            ],
            "allow_power_analysis_consuming_this_report": policy[
                "allow_power_analysis_consuming_this_report"
            ],
            "recommended_next_step": policy["recommended_next_step"],
        }
    )


@app.command("report")
def report_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory to scan."),
    output_path: Path = typer.Option(..., "-o", "--output", help="Output HTML path."),
    analysis: Optional[Path] = typer.Option(
        None, "--analysis", help="Optional analysis JSON from 'analyze' command."
    ),
    title: str = typer.Option(
        "Wutai Clinic Evidence Report",
        "--title",
        help="Report title shown in the HTML header.",
    ),
) -> None:
    """Generate a zero-dependency static HTML evidence report."""
    from wutai_clinic.reporting.html_report import write_html_report

    result = write_html_report(
        root=evidence_root,
        output_path=output_path,
        analysis_path=analysis,
    )
    _emit_json(
        {
            "output_path": result["output_path"],
            "pairs_found": result["pairs_found"],
            "nodes_found": result["nodes_found"],
            "edges_found": result["edges_found"],
            "truncated": result["truncated"],
            "generated_at": result["generated_at"],
        }
    )


@app.command("fresh-target-harvest")
def fresh_target_harvest_cmd(
    evidence_index: Path = typer.Option(..., "--evidence-index"),
    dataset_instances: Path = typer.Option(..., "--dataset-instances"),
    lite300_report: Path = typer.Option(..., "--lite300-report"),
    max_instances: int = typer.Option(30, "--max-instances"),
    output_dir: Path = typer.Option(..., "-o"),
    execute: bool = typer.Option(False, "--execute"),
    ack_docker: bool = typer.Option(False, "--ack-docker"),
    ack_external_provider: bool = typer.Option(False, "--ack-external-provider"),
) -> None:
    """Harvest fresh failure targets from SWE-bench_Verified for Protocol v2.

    Default (plan mode): pure offline, no Docker, no external calls.
    Execute mode: requires --execute --ack-docker --ack-external-provider.
    """
    if execute:
        plan_path = output_dir / "fresh_target_harvest_plan.json"
        if not plan_path.exists():
            typer.echo(
                f"Execute mode requires a plan at {plan_path}. "
                "Run plan mode first (without --execute).",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            result = run_fresh_target_harvest(
                plan_path=plan_path,
                runner=_harvest_unimplemented_runner,
                output_dir=output_dir,
                ack_docker=ack_docker,
                ack_external_provider=ack_external_provider,
            )
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        _emit_json(
            {
                "decision": result["decision"],
                "harvest_candidates": len(result["harvest_candidates"]),
                "success_sentinels": len(result["success_sentinels"]),
                "harvest_errors": len(result["harvest_errors"]),
                "report_path": str(result["report_path"]),
            }
        )
    else:
        try:
            result = write_fresh_target_harvest_plan(
                evidence_index_path=evidence_index,
                dataset_instances_path=dataset_instances,
                lite300_report_path=lite300_report,
                max_instances=max_instances,
                output_dir=output_dir,
            )
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        _emit_json(
            {
                "decision": result["decision"],
                "selected": len(result["selected_instances"]),
                "report_path": str(result["report_path"]),
            }
        )


def _harvest_unimplemented_runner(instance_id: str, output_dir: Path) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError(
        f"No baseline runner configured for instance {instance_id}. "
        "Wire a real runner via run_fresh_target_harvest(runner=...) before executing."
    )


@app.command("batch-orchestrate")
def batch_orchestrate_cmd(
    batch_spec: Path = typer.Argument(..., help="Path to batch spec JSON file."),
    state_dir: Path = typer.Option(..., "-o", "--state-dir"),
    status: bool = typer.Option(False, "--status"),
    advance: bool = typer.Option(False, "--advance"),
) -> None:
    """Orchestrate a Protocol v2 batch: advance offline steps, surface operator commands.

    Never exposes --ack-docker, --ack-external-provider, or --ack-official-eval.
    Authorization gates remain human-held.
    """
    spec = json.loads(batch_spec.read_text(encoding="utf-8"))
    if status and not advance:
        _emit_json(batch_status(spec, state_dir))
        return
    if advance:
        _emit_json(advance_batch(spec, state_dir))
        return
    typer.echo("Specify --status or --advance.", err=True)
    raise typer.Exit(code=1)


@app.command()
def count(input: Path = typer.Argument(..., help="JSONL file to count.")) -> None:
    """Count non-empty JSONL rows."""
    typer.echo(count_jsonl(input))


@app.command("mechanistic-endpoints")
def mechanistic_endpoints_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
    dataset_name: str = typer.Option("SWE-bench/SWE-bench_Lite", "--dataset-name"),
    split: str = typer.Option("test", "--split"),
    offline_gold: Optional[Path] = typer.Option(
        None,
        "--offline-gold",
        help="Optional JSONL of {instance_id, patch} rows; bypasses the local HF cache.",
    ),
) -> None:
    """Retroactive secondary mechanistic endpoints for completed Protocol v2 pairs.

    Fully offline: gold patches come from --offline-gold or the local HF cache
    (HF_DATASETS_OFFLINE semantics); no network, Docker, or provider calls.
    """
    from wutai_clinic.engine.mechanistic_endpoints import (
        write_mechanistic_endpoints_evidence,
    )

    result = write_mechanistic_endpoints_evidence(
        evidence_root,
        output_dir,
        offline_gold_path=offline_gold,
        dataset_name=dataset_name,
        split=split,
    )
    report = result["report"]
    summary = report["summary"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "output_dir": str(output_dir),
            "report": str(result["report_path"]),
            "pairs": str(result["pairs_path"]),
            "manifest": str(result["manifest_path"]),
            "pair_count": summary["pair_count"],
            "strict_fresh_pair_count": summary["strict_fresh_pair_count"],
            "reference_pair_count": summary["reference_pair_count"],
            "diverged_pair_count": summary["diverged_pair_count"],
            "gold_available_pair_count": summary["gold_available_pair_count"],
        }
    )


@app.command("epsilon-estimate")
def epsilon_estimate_cmd(
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
    rerun_root: Optional[Path] = typer.Option(
        None,
        "--rerun-root",
        help="protocol_v2_epsilon_rerun/<instance> dir holding run_*/.../report.json files.",
    ),
    instance_id: Optional[str] = typer.Option(None, "--instance-id"),
    outcomes: Optional[str] = typer.Option(
        None,
        "--outcomes",
        help="Comma list of rerun resolved flags (e.g. '0,0,0'); alternative to --rerun-root.",
    ),
    reference_outcome: bool = typer.Option(False, "--reference-outcome"),
    target_uplift_rate: float = typer.Option(0.2, "--target-uplift-rate"),
    trigger_hit_rate: float = typer.Option(1.0, "--trigger-hit-rate"),
    inventory_note: Optional[str] = typer.Option(
        None,
        "--inventory-note",
        help="Free-text record of pre-existing rerun evidence found during inventory.",
    ),
) -> None:
    """Estimate the substrate noise floor (epsilon) from pure control-arm reruns.

    Fully offline: consumes existing rerun outcomes; never executes anything.
    """
    from wutai_clinic.engine.epsilon import (
        flip_rate_estimate,
        scan_rerun_outcomes,
        write_epsilon_evidence,
    )

    estimates: dict[str, dict] = {}
    if outcomes is not None:
        flags = [token.strip() in {"1", "true", "True"} for token in outcomes.split(",") if token.strip()]
        estimates[instance_id or "manual"] = flip_rate_estimate(
            flags, reference_outcome=reference_outcome
        )
    if rerun_root is not None:
        if instance_id is None:
            typer.echo("--rerun-root requires --instance-id", err=True)
            raise typer.Exit(code=1)
        scanned = scan_rerun_outcomes(rerun_root, instance_id)
        estimates[instance_id] = flip_rate_estimate(
            scanned, reference_outcome=reference_outcome
        )
    inventory = (
        [{"note": inventory_note}] if inventory_note else []
    )
    result = write_epsilon_evidence(
        output_dir,
        estimates=estimates,
        existing_rerun_inventory=inventory,
        target_uplift_rate=target_uplift_rate,
        trigger_hit_rate=trigger_hit_rate,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "per_instance_estimates": report["per_instance_estimates"],
            "pooled_estimate": report["pooled_estimate"],
        }
    )


@app.command("oracle-probe-prepare")
def oracle_probe_prepare_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    source_task_id: str = typer.Option(..., "--source-task-id"),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
    offline_gold: Optional[Path] = typer.Option(None, "--offline-gold"),
    dataset_name: str = typer.Option("SWE-bench/SWE-bench_Lite", "--dataset-name"),
    split: str = typer.Option("test", "--split"),
    distillation_level: str = typer.Option("guidance", "--distillation-level"),
) -> None:
    """Prepare an oracle-capsule probe arm (offline; contaminated-by-design layer).

    Generates the capsule + cloned runtime config; live execution stays gated
    behind the existing sweagent-protocol-v2-live-single --ack-* flow.
    """
    from wutai_clinic.engine.mechanistic_endpoints import load_gold_patches
    from wutai_clinic.intervention.oracle_capsule import write_oracle_probe_prepare_evidence

    gold_patches = load_gold_patches(
        [source_task_id],
        offline_gold_path=offline_gold,
        dataset_name=dataset_name,
        split=split,
    )
    result = write_oracle_probe_prepare_evidence(
        evidence_root,
        source_task_id=source_task_id,
        output_dir=output_dir,
        gold_patches=gold_patches,
        distillation_level=distillation_level,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "config": str(result["config_path"]),
            "capsule": str(result["capsule_path"]),
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "contaminated_by_design": report["contaminated_by_design"],
        }
    )


@app.command("oracle-probe-outcome")
def oracle_probe_outcome_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    source_task_id: str = typer.Option(..., "--source-task-id"),
    oracle_eval_report: Path = typer.Option(
        ..., "--oracle-eval-report", help="swebench per-instance report.json of the oracle arm."
    ),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
    variant: str = typer.Option(
        "with_replay_prefix",
        "--variant",
        help="Arm variant label (e.g. dose_detailed, dose_verbatim) for the excluded listing.",
    ),
) -> None:
    """Three-arm oracle probe comparison report (offline)."""
    from wutai_clinic.intervention.oracle_capsule import write_oracle_probe_outcome_evidence

    result = write_oracle_probe_outcome_evidence(
        evidence_root,
        source_task_id=source_task_id,
        oracle_eval_report_path=oracle_eval_report,
        output_dir=output_dir,
        variant=variant,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "three_arm_outcomes": report["three_arm_outcomes"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "contaminated_by_design": report["contaminated_by_design"],
        }
    )


@app.command("oracle-probe-replay-free-prepare")
def oracle_probe_replay_free_prepare_cmd(
    probe_config: Path = typer.Argument(
        ..., help="Existing oracle_probe_runtime_config.json from oracle-probe-prepare."
    ),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Clone an oracle probe config as the task10 replay-free variant (offline)."""
    from wutai_clinic.intervention.oracle_capsule import build_replay_free_variant_config

    base = json.loads(probe_config.read_text(encoding="utf-8"))
    variant = build_replay_free_variant_config(
        base, native_output_dir=output_dir / "native"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "oracle_probe_replay_free_runtime_config.json"
    config_path.write_text(
        json.dumps(variant, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    _emit_json(
        {
            "config": str(config_path),
            "variant": "replay_free",
            "contaminated_by_design": True,
        }
    )


@app.command("evidence-manifest")
def evidence_manifest_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence directory to hash."),
    verify: bool = typer.Option(False, "--verify", help="Verify instead of generate."),
    note: Optional[str] = typer.Option(None, "--note", help="Header note for generation."),
) -> None:
    """Generate or verify the sha256 MANIFEST for an evidence tree (offline)."""
    from wutai_clinic.evidence.manifest_tool import (
        generate_manifest_file,
        verify_manifest_file,
    )

    if verify:
        result = verify_manifest_file(evidence_root)
        _emit_json(
            {
                "ok": result["ok"],
                "manifest": str(result["manifest_path"]),
                "expected_count": result.get("expected_count"),
                "mismatched": result.get("mismatched"),
                "missing": result.get("missing"),
                "untracked": result.get("untracked"),
                "error": result.get("error"),
            }
        )
        if not result["ok"]:
            raise typer.Exit(code=1)
    else:
        result = generate_manifest_file(evidence_root, note=note)
        _emit_json(
            {
                "manifest": str(result["manifest_path"]),
                "file_count": result["file_count"],
                "total_bytes": result["total_bytes"],
            }
        )


@app.command("post-repair-outcomes")
def post_repair_outcomes_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Assemble re-measured outcomes on the repaired substrate (offline)."""
    from wutai_clinic.engine.post_repair_outcomes import write_post_repair_outcomes_evidence

    result = write_post_repair_outcomes_evidence(evidence_root, output_dir)
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "harm_pair_count": report["harm_pair_count"],
            "harm_pair_ids": report["harm_pair_ids"],
            "pair_rows": report["pair_rows"],
            "epsilon_estimates": report["epsilon_estimates"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("instance-validity")
def instance_validity_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Classify eval-substrate validity per instance from gold sanity reports (offline)."""
    from wutai_clinic.engine.instance_validity import write_instance_validity_evidence

    result = write_instance_validity_evidence(evidence_root, output_dir)
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "valid_instances": report["valid_instances"],
            "invalid_instances": report["invalid_instances"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("wave3-synthesis")
def wave3_synthesis_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Fact-checked wave3 synthesis report from the four evidence lines (offline)."""
    from wutai_clinic.engine.wave3_synthesis import write_wave3_synthesis_evidence

    result = write_wave3_synthesis_evidence(evidence_root, output_dir)
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "finding_count": len(report["synthesis"]["findings"]),
            "report": str(result["report_path"]),
            "markdown": str(result["markdown_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )


@app.command("oracle-probe-replay-free-outcome")
def oracle_probe_replay_free_outcome_cmd(
    evidence_root: Path = typer.Argument(..., help="Evidence root directory (read-only)."),
    source_task_id: str = typer.Option(..., "--source-task-id"),
    oracle_eval_report: Path = typer.Option(
        ..., "--oracle-eval-report", help="swebench per-instance report.json of the replay-free arm."
    ),
    replay_free_patch: Path = typer.Option(
        ..., "--replay-free-patch", help="Patch produced by the replay-free arm."
    ),
    offline_gold: Optional[Path] = typer.Option(None, "--offline-gold"),
    dataset_name: str = typer.Option("SWE-bench/SWE-bench_Lite", "--dataset-name"),
    split: str = typer.Option("test", "--split"),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
) -> None:
    """Task10 replay-free typing report (offline, preregistered three-way matrix)."""
    from wutai_clinic.engine.mechanistic_endpoints import load_gold_patches
    from wutai_clinic.intervention.oracle_capsule import (
        write_oracle_probe_replay_free_outcome_evidence,
    )

    gold_patches = load_gold_patches(
        [source_task_id],
        offline_gold_path=offline_gold,
        dataset_name=dataset_name,
        split=split,
    )
    result = write_oracle_probe_replay_free_outcome_evidence(
        evidence_root,
        source_task_id=source_task_id,
        oracle_eval_report_path=oracle_eval_report,
        replay_free_patch_path=replay_free_patch,
        gold_patches=gold_patches,
        output_dir=output_dir,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "proximity": report["proximity"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
            "contaminated_by_design": report["contaminated_by_design"],
        }
    )


def _resolved_flag_from_eval_report(report_path: Path, instance_id: str) -> bool:
    """Read a single swebench-style report.json's resolved flag for one instance."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    instance = payload.get(instance_id)
    if not isinstance(instance, dict) or "resolved" not in instance:
        raise typer.BadParameter(
            f"{report_path}: no resolved flag for instance {instance_id}"
        )
    return bool(instance["resolved"])


@app.command("instrument-sensitivity-outcome")
def instrument_sensitivity_outcome_cmd(
    source_task_id: str = typer.Option(..., "--source-task-id"),
    distillation_level: str = typer.Option(
        ..., "--distillation-level", help="Dose tier: guidance | detailed | verbatim."
    ),
    output_dir: Path = typer.Option(..., "-o", "--output-dir"),
    control_rerun_root: Optional[Path] = typer.Option(
        None,
        "--control-rerun-root",
        help="Dir holding control run_*/**/report.json (the deterministic-failure noise floor).",
    ),
    control_outcomes: Optional[str] = typer.Option(
        None,
        "--control-outcomes",
        help="Comma list of control resolved flags (e.g. '0,0,0,0,0'); alternative to --control-rerun-root.",
    ),
    treatment_eval_report: list[Path] = typer.Option(
        [],
        "--treatment-eval-report",
        help="Repeatable: each treatment-arm swebench report.json (one per rep).",
    ),
    alpha: float = typer.Option(0.05, "--alpha"),
) -> None:
    """Positive-control instrument-sensitivity report (offline, uplift direction).

    Contaminated-by-design layer (oracle-derived hints). Supports ONLY an
    instrument-sensitivity claim; never any uplift/harm/effectiveness claim.
    Live execution stays gated behind sweagent-protocol-v2-live-single --ack-*.
    """
    from wutai_clinic.engine.epsilon import scan_rerun_outcomes
    from wutai_clinic.engine.sensitivity import write_instrument_sensitivity_evidence

    if control_rerun_root is not None:
        control = scan_rerun_outcomes(control_rerun_root, source_task_id)
        control_note = f"scanned from {control_rerun_root.as_posix()}"
    elif control_outcomes is not None:
        control = [t.strip() in {"1", "true", "True"} for t in control_outcomes.split(",") if t.strip()]
        control_note = "supplied via --control-outcomes"
    else:
        raise typer.BadParameter("provide --control-rerun-root or --control-outcomes")

    treatment = [
        _resolved_flag_from_eval_report(p, source_task_id) for p in treatment_eval_report
    ]

    result = write_instrument_sensitivity_evidence(
        output_dir,
        source_task_id=source_task_id,
        distillation_level=distillation_level,
        control_outcomes=control,
        treatment_outcomes=treatment,
        alpha=alpha,
        control_lineage_note=control_note,
    )
    report = result["report"]
    _emit_json(
        {
            "decision": report["decision"],
            "passed": report["passed"],
            "classification": report["classification"],
            "contaminated_by_design": report["contaminated_by_design"],
            "report": str(result["report_path"]),
            "manifest": str(result["manifest_path"]),
        }
    )
