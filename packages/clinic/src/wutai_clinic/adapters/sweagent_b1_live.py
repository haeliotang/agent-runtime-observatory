"""Route B1 SWE-agent live-single adapter (Amendment A).

Plans (offline, default) or executes (gated, lazy SWE-agent import) one B1 arm.
B1 is replay-free: treatment injects the issue-text reproduction ONCE at the
first model query; control injects nothing. The M2b content leak-scan runs as a
HARD pre-run gate — a treatment payload that overlaps FAIL_TO_PASS / test_patch /
gold is blocked before any provider/Docker call, so a leaking arm can never run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from wutai_clinic.adapters.base import RuntimePermissionPolicy
from wutai_clinic.adapters.sweagent_live import load_mapping_file
from wutai_clinic.intervention.b1_issue_repro import (
    ReproCaptureExecutor,
    b1_payload_leak_scan,
)
from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.intervention.hybrid_runner import HybridReplayGenerationModel
from wutai_clinic.intervention.protocol_b1 import ProtocolB1
from wutai_clinic.intervention.protocol_b1_hook import (
    ProtocolB1InjectionHook,
    ProtocolB1InjectionVoid,
)
from wutai_clinic.io import write_jsonl
from wutai_clinic.io.report import generate_manifest, generate_report, sha256_file

SWEAGENT_B1_LIVE_SINGLE_PHASE = "route_b.b1_sweagent_live_single"
SWEAGENT_B1_LIVE_SINGLE_VERSION = "route_b1_sweagent_live_single_v1"
BOUNDARY = (
    "Plans or executes one Route B1 SWE-agent live-single arm. Replay-free; treatment "
    "injects the issue-text reproduction once. Execution requires explicit Docker + "
    "external-provider acks. M2b leak-scan blocks any payload overlapping FAIL_TO_PASS / "
    "test_patch / gold. No uplift claim (B6 unchanged); go/no-go evidence only."
)

RunSingleFactory = Callable[[Path], Any]
B1ArmType = Literal["control", "treatment"]


@dataclass(frozen=True)
class B1LeakRefs:
    """Forbidden references used ONLY by the M2b scanner — never injected, never persisted."""

    fail_to_pass: list[str] = field(default_factory=list)
    test_patch: str | None = None
    gold_patch: str | None = None


@dataclass(frozen=True)
class SWEAgentB1LiveSingleSpec:
    config_path: Path
    output_dir: Path
    protocol: ProtocolB1
    arm_type: B1ArmType = "treatment"
    payload: dict[str, Any] | None = None
    leak_refs: B1LeakRefs = field(default_factory=B1LeakRefs)
    execute: bool = False
    source_task_id: str | None = None
    pair_id: str | None = None
    require_official_eval: bool = False
    # Injected for testing; in real execute the adapter builds one from the container.
    repro_executor: ReproCaptureExecutor | None = None


def _load_run_single_from_config(config_path: Path) -> Any:
    try:
        from sweagent.run.run_single import RunSingle, RunSingleConfig
    except Exception as exc:  # pragma: no cover - optional live dependency
        raise RuntimeError("SWE-agent run_single is required for execute=true") from exc

    payload = load_mapping_file(config_path)
    config = RunSingleConfig.model_validate(payload)
    return RunSingle.from_config(config)


def _make_env_executor(run_single: Any) -> ReproCaptureExecutor:  # pragma: no cover - live container
    """Run an issue-derived repro script in the LIVE SWE-bench container via the env
    runtime (swerex RexCommand, same path as SWEEnvRuntimeProbe). Issue-text only;
    never the official test. Called by the hook at first model query (env is live)."""
    import asyncio

    def _run(script: str) -> str:
        from swerex.runtime.abstract import Command as RexCommand

        env = getattr(run_single, "env", None)
        runtime = getattr(getattr(env, "deployment", None), "runtime", None)
        if runtime is None or not hasattr(runtime, "execute"):
            raise RuntimeError("SWE-agent env.deployment.runtime.execute is required for repro capture")
        # write the issue-derived repro to a file, run it, capture combined output
        written = asyncio.run(
            runtime.execute(
                RexCommand(
                    command="cat > /tmp/b1_repro.py <<'B1_REPRO_EOF'\n" + script + "\nB1_REPRO_EOF",
                    timeout=60, shell=True, check=False,
                )
            )
        )
        _ = written
        resp = asyncio.run(
            runtime.execute(
                RexCommand(command="python /tmp/b1_repro.py", timeout=120, shell=True, check=False, merge_output_streams=True)
            )
        )
        out = str(getattr(resp, "stdout", "") or "")
        err = str(getattr(resp, "stderr", "") or "")
        return (out + "\n" + err).strip()

    return _run


def _extract_submission(result: Any) -> str | None:
    """The agent's final patch (git diff) — SWE-agent puts it in result.info.submission.
    Archiving it per-arm/rep is what lets official eval separate control vs treatment."""
    info = getattr(result, "info", None)
    if isinstance(info, dict) and "submission" in info:
        return info.get("submission")
    return getattr(result, "submission", None)


def _extract_exit_status(result: Any) -> str | None:
    """Pull SWE-agent's exit_status from a run result. A run that ended in
    `exit_error` / `exit_api` (e.g. provider 'Insufficient Balance') is NOT a
    clean completion — RunSingle.run() swallows it and returns normally, so the
    adapter must read this to avoid counting a crashed run as a real empty-fail."""
    info = getattr(result, "info", None)
    if isinstance(info, dict) and info.get("exit_status") is not None:
        return str(info["exit_status"])
    status = getattr(result, "exit_status", None)
    return str(status) if status is not None else None


def _read_native_outcome(run_single: Any) -> dict[str, Any]:
    """RunSingle.run() returns None — SWE-agent saves the patch + trajectory to
    output_dir/<problem_id>/ via save_predictions(). Read the patch (.pred
    model_patch) and exit_status (.traj info.exit_status) from there; this is the
    authoritative source (the run result object is not returned to us)."""
    out: dict[str, Any] = {"submission": None, "exit_status": None}
    base = getattr(run_single, "output_dir", None)
    pid = getattr(getattr(run_single, "problem_statement", None), "id", None)
    if base is None or pid is None:
        return out
    d = Path(base) / str(pid)
    pred, traj = d / f"{pid}.pred", d / f"{pid}.traj"
    if pred.is_file():
        try:
            out["submission"] = json.loads(pred.read_text(encoding="utf-8")).get("model_patch")
        except Exception:  # pragma: no cover - defensive
            pass
    if traj.is_file():
        try:
            out["exit_status"] = (json.loads(traj.read_text(encoding="utf-8")).get("info") or {}).get("exit_status")
        except Exception:  # pragma: no cover - defensive
            pass
    return out


def _artifact(path: Path) -> dict[str, Any]:
    record_count = None
    if path.suffix == ".jsonl" and path.is_file():
        with path.open("rb") as handle:
            record_count = sum(1 for line in handle if line.strip())
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "record_count": record_count,
        "exists": path.is_file(),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def run_sweagent_b1_live_single(
    *,
    spec: SWEAgentB1LiveSingleSpec,
    policy: RuntimePermissionPolicy,
    run_single_factory: RunSingleFactory | None = None,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    run_single_factory = run_single_factory or _load_run_single_from_config
    config_exists = spec.config_path.is_file()
    is_treatment = spec.arm_type == "treatment"

    # --- M2b HARD GATE: scan the treatment payload before anything live --------
    leak_findings: list[str] = []
    if is_treatment:
        if not spec.payload:
            leak_findings = ["treatment_payload_missing"]
        else:
            leak_findings = b1_payload_leak_scan(
                spec.payload,
                fail_to_pass=spec.leak_refs.fail_to_pass,
                test_patch=spec.leak_refs.test_patch,
                gold_patch=spec.leak_refs.gold_patch,
            )
    payload_clean = not leak_findings

    authorized = policy.allows(
        require_docker=True,
        require_external_provider=True,
        require_official_eval=spec.require_official_eval,
    )
    gates = {
        "config_path_exists": config_exists,
        "protocol_b1_valid": True,
        "arm_is_control_or_treatment": spec.arm_type in ("control", "treatment"),
        "replay_free": True,
        "control_injects_nothing": not is_treatment or True,
        "treatment_payload_present": not is_treatment or bool(spec.payload),
        "m2b_payload_leak_scan_clean": payload_clean,
        "payload_provenance_issue_text_only": (
            not is_treatment or (spec.payload or {}).get("payload_provenance") == "issue_text_only"
        ),
        "docker_ack_if_execute": not spec.execute or policy.allow_docker,
        "external_provider_ack_if_execute": not spec.execute or policy.allow_external_provider,
        "official_eval_ack_if_required": not spec.require_official_eval or policy.allow_official_eval,
        "raw_payload_logging_disabled": spec.protocol.guard.raw_payload_logging is False,
        "uplift_claim_not_made": spec.protocol.guard.uplift_claim_allowed is False,
    }

    should_run = spec.execute and authorized and config_exists and payload_clean
    hook: ProtocolB1InjectionHook | None = None
    run_error = None
    run_result_type = None
    injection_void = None
    run_single_started = False
    effective_payload = spec.payload
    capture_traceback = None
    capture_leak_findings: list[str] = []
    run_exit_status = None
    submission = None

    if should_run:
        run_single = None
        try:
            run_single = run_single_factory(spec.config_path)
            agent = getattr(run_single, "agent", None)
            if agent is None or not hasattr(agent, "add_hook"):
                raise RuntimeError("RunSingle agent must expose add_hook")
            original_model = getattr(agent, "model", None)
            if original_model is None or not hasattr(original_model, "query"):
                raise RuntimeError("RunSingle agent.model must expose query")
            # replay-free: empty replay prefix => fully live from step 0.
            agent.model = HybridReplayGenerationModel(replay_actions=[], delegate=original_model)
            if is_treatment:
                # Capture + inject happen INSIDE the hook at the first model query,
                # when the container is live (pre-run capture raised DeploymentNotStarted).
                hook = ProtocolB1InjectionHook(
                    protocol=spec.protocol,
                    issue_reproduction_steps=(spec.payload or {}).get("issue_reproduction_steps") or "",
                    source_task_id=spec.source_task_id,
                    pair_id=spec.pair_id,
                    capture_executor=spec.repro_executor or _make_env_executor(run_single),
                    fail_to_pass=spec.leak_refs.fail_to_pass,
                    test_patch=spec.leak_refs.test_patch,
                    gold_patch=spec.leak_refs.gold_patch,
                )
                agent.add_hook(hook)
            run_single_started = True
            result = run_single.run()  # SWE-agent's RunSingle.run() returns None
            run_result_type = type(result).__name__
            # authoritative source is the saved native output on disk; fall back
            # to the result object (for tests / other SWE-agent versions).
            native = _read_native_outcome(run_single)
            run_exit_status = native["exit_status"] if native["exit_status"] is not None else _extract_exit_status(result)
            submission = native["submission"] if native["submission"] is not None else _extract_submission(result)
            if hook is not None:  # read what the hook captured/injected DURING the run
                capture_traceback = hook.captured_traceback
                capture_leak_findings = hook.capture_leak_findings
                if hook.payload is not None:
                    effective_payload = hook.payload
        except ProtocolB1InjectionVoid as exc:  # pragma: no cover - hook records, does not raise
            injection_void = exc.event
        except Exception as exc:  # pragma: no cover - environment-specific live failure path
            run_error = f"{type(exc).__name__}: {exc}"

    # A run whose SWE-agent exit_status carries an error (exit_error / exit_api,
    # e.g. provider 'Insufficient Balance') is NOT a clean completion.
    run_exit_ok = run_exit_status is None or "error" not in str(run_exit_status).lower()
    hook_events = hook.audit_events if hook is not None else []
    gates.update(
        {
            "no_unrequested_run": spec.execute or not should_run,
            "run_started_if_execute": not should_run or run_single_started,
            "injection_hook_attached_if_treatment_execute": (
                not should_run or not is_treatment or hook is not None
            ),
            "exactly_one_injection_if_treatment_run": (
                not (should_run and is_treatment and run_single_started and run_error is None)
                or (hook is not None and hook.injection_count == 1)
            ),
            "run_error_absent": run_error is None,
            "run_exit_status_clean": run_exit_ok,
        }
    )

    if is_treatment and not payload_clean:
        # A leak is fatal regardless of mode — never let "planned" mask it.
        decision = "route_b1_live_arm_blocked_payload_leak"
    elif not spec.execute:
        decision = "route_b1_live_arm_planned_no_run"
    elif not authorized:
        decision = "route_b1_live_arm_blocked_needs_ack"
    elif not config_exists:
        decision = "route_b1_live_arm_blocked_missing_config"
    elif injection_void is not None:
        decision = "route_b1_live_arm_injection_void"
    elif run_error is not None or not run_exit_ok:
        decision = "route_b1_live_arm_run_failed"
    elif is_treatment and capture_leak_findings:
        # run completed but the live-captured repro overlapped the official test/fix
        # -> treatment voided (route-b1-cells excludes it via leak_clean).
        decision = "route_b1_live_arm_capture_leak_void"
    else:
        decision = "route_b1_live_arm_run_completed"

    arm_complete = (
        should_run and run_single_started and run_error is None and injection_void is None and run_exit_ok
    )

    protocol_path = spec.output_dir / "b1_live_arm_protocol.json"
    payload_path = spec.output_dir / "b1_live_arm_payload.json"
    events_path = spec.output_dir / "b1_live_arm_events.jsonl"
    patch_path = spec.output_dir / "b1_live_arm.patch"
    report_path = spec.output_dir / "b1_live_arm_report.json"
    manifest_path = spec.output_dir / "b1_live_arm_manifest.json"

    _write_json(protocol_path, spec.protocol.to_dict())
    # Persist the deployable payload (issue-text only, incl. captured traceback in
    # execute). leak_refs are NEVER written.
    if is_treatment and effective_payload:
        _write_json(payload_path, effective_payload)
    write_jsonl(events_path, [{"event_type": "protocol_b1_injection", **e} for e in hook_events])
    # Archive the agent's patch so official eval can read THIS arm/rep directly.
    patch_text = submission or ""
    if submission is not None:
        patch_path.write_text(patch_text, encoding="utf-8")
    patch_non_empty = bool(patch_text.strip())

    report = generate_report(
        phase=SWEAGENT_B1_LIVE_SINGLE_PHASE,
        decision=decision,
        gate_results=gates,
        extras={
            "version": SWEAGENT_B1_LIVE_SINGLE_VERSION,
            "claim_boundary": BOUNDARY,
            "source_task_id": spec.source_task_id,
            "pair_id": spec.pair_id,
            "arm_type": spec.arm_type,
            "execute_requested": spec.execute,
            "run_single_started": run_single_started,
            "replay_free": True,
            "protocol_hash": spec.protocol.protocol_hash,
            "payload_hash": stable_json_hash(effective_payload) if effective_payload else None,
            "m2b_leak_findings": leak_findings,
            "m2b_capture_leak_findings": capture_leak_findings,
            "issue_repro_traceback_captured": capture_traceback is not None,
            "injection_count": hook.injection_count if hook is not None else 0,
            "injection_void": injection_void,
            "run_result_type": run_result_type,
            "run_error": run_error,
            "run_exit_status": run_exit_status,
            "run_exit_ok": run_exit_ok,
            "patch_path": patch_path.as_posix() if submission is not None else None,
            "patch_bytes": len(patch_text.encode("utf-8")),
            "patch_non_empty": patch_non_empty,
            "patch_sha256": stable_json_hash(patch_text) if submission is not None else None,
            "official_eval_completed": False,
            "continuation_policy": {
                "allow_pair_assembly": arm_complete and all(gates.values()),
                "allow_route_b1_real_run_without_ack": False,
                "allow_uplift_claim": False,
                "recommended_next_step": (
                    "execute_control_and_treatment_arms_after_explicit_authorization"
                    if not spec.execute
                    else "fix_payload_leak_before_rerun"
                    if is_treatment and not payload_clean
                    else "assemble_b1_pair_if_both_arms_complete"
                ),
            },
        },
    )
    _write_json(report_path, report)
    manifest = generate_manifest(
        phase=SWEAGENT_B1_LIVE_SINGLE_PHASE,
        report=report,
        artifacts=[
            _artifact(p)
            for p in [spec.config_path, protocol_path, payload_path, events_path, patch_path, report_path]
        ],
    )
    manifest["version"] = SWEAGENT_B1_LIVE_SINGLE_VERSION
    _write_json(manifest_path, manifest)
    return {
        "report": report,
        "manifest": manifest,
        "hook_events": hook_events,
        "leak_findings": leak_findings,
        "protocol_path": protocol_path,
        "payload_path": payload_path,
        "events_path": events_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }


__all__ = [
    "SWEAGENT_B1_LIVE_SINGLE_VERSION",
    "B1LeakRefs",
    "SWEAgentB1LiveSingleSpec",
    "run_sweagent_b1_live_single",
]
