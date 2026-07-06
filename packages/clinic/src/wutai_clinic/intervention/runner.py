from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wutai_clinic.intervention.hooks import stable_json_hash
from wutai_clinic.schemas import InterventionPair, InterventionResult


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "Wutai_observatory").is_dir():
            return parent
    return Path(__file__).resolve().parents[5]


ROOT = _repo_root()
MODELS = ROOT / "Wutai_observatory/models"
SWE_AGENT_SRC = ROOT / "Wutai_observatory/software-agent-sdk-main/swe_agent_src"
DEFAULT_PROVIDER_CONFIG_SOURCE = (
    ROOT / "Wutai_observatory/software-agent-sdk-main/scripts/harvest_gpt5_5_engineering.py"
)
DEFAULT_SWE_AGENT_CONFIG = SWE_AGENT_SRC / "config/default.yaml"
DEFAULT_SMOKE_ROOT = MODELS / "phase315_one_pair_smoke_runs"
DEFAULT_BATCH_RUN_ROOT = MODELS / "phase316_paired_uplift_runs"
EXPECTED_PROVIDER_MODEL = "gpt-5.5"
DEFAULT_SMOKE_PER_INSTANCE_CALL_LIMIT = 50
DEFAULT_BATCH_PER_ARM_CALL_LIMIT = 50
PHASE315_SMOKE_VERSION = "phase315_one_pair_smoke_wrapper_v1"
PHASE316_BATCH_VERSION = "phase316_batch_execution_wrapper_v1"
RAW_GENERATED_SUFFIXES = {".traj", ".log"}
RAW_GENERATED_NAMES = {"run_batch.config.yaml", "model_input_debug.log"}
RAW_GENERATED_NAME_SUFFIXES = {".config.yaml"}
TRUSTED_PROVIDER_HOSTS = {"api.openai.com"}


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_provider_assignments(provider_config_source: Path) -> dict[str, str | None]:
    tree = ast.parse(provider_config_source.read_text())
    values: dict[str, str | None] = {
        "PROXY_API_KEY": None,
        "PROXY_BASE_URL": None,
        "MODEL_PRIMARY": None,
    }
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id in values
            and isinstance(node.value, ast.Constant)
        ):
            if isinstance(node.value.value, str):
                values[target.id] = node.value.value
    return values


def provider_summary(
    provider_config_source: Path = DEFAULT_PROVIDER_CONFIG_SOURCE,
    *,
    acknowledge_external_export: bool = False,
) -> dict[str, Any]:
    assignments = parse_provider_assignments(provider_config_source)
    api_base = assignments["PROXY_BASE_URL"] or ""
    api_base_host = urlparse(api_base).netloc
    custom_external_base = api_base_host not in TRUSTED_PROVIDER_HOSTS
    return {
        "provider_config_source": relative(provider_config_source),
        "provider_config_source_sha256": sha256_file(provider_config_source),
        "provider_config_policy": "reuse_existing_gpt55_provider_config_redacted",
        "declared_model": assignments["MODEL_PRIMARY"],
        "declares_api_key": bool(assignments["PROXY_API_KEY"]),
        "declares_api_base": bool(assignments["PROXY_BASE_URL"]),
        "api_base_origin": "trusted_official_provider"
        if not custom_external_base
        else "custom_external_api_base",
        "api_base_host_sha256": stable_json_hash(api_base_host) if api_base_host else None,
        "external_prompt_export_risk": custom_external_base,
        "external_prompt_export_acknowledged": acknowledge_external_export,
        "external_prompt_export_policy": (
            "explicit_ack_required_before_real_run"
            if custom_external_base
            else "no_extra_ack_required"
        ),
        "secret_material_stored": False,
        "raw_provider_config_stored": False,
    }


def first_pair_id(bridge_plan: list[dict[str, Any]]) -> str:
    return str(sorted(bridge_plan, key=lambda row: int(row["execution_index"]))[0]["pair_id"])


def select_pair_rows(
    bridge_plan: list[dict[str, Any]], pair_id: str | None = None
) -> list[dict[str, Any]]:
    selected_pair_id = pair_id or first_pair_id(bridge_plan)
    rows = [row for row in bridge_plan if row.get("pair_id") == selected_pair_id]
    return sorted(rows, key=lambda row: int(row["execution_index"]))


def arm_output_dir(run_root: Path, row: dict[str, Any]) -> Path:
    return run_root / str(row["pair_id"]) / str(row["arm_type"])


def runner_args_for_arm(
    *,
    row: dict[str, Any],
    run_root: Path,
    swe_agent_config: Path = DEFAULT_SWE_AGENT_CONFIG,
    provider_model: str | None = None,
    per_instance_call_limit: int = DEFAULT_SMOKE_PER_INSTANCE_CALL_LIMIT,
) -> list[str]:
    model_name = provider_model or row.get("provider_model") or EXPECTED_PROVIDER_MODEL
    args = [
        "--config",
        str(swe_agent_config),
        "--agent.model.name",
        "openai/" + str(model_name),
        "--instances.type",
        "swe_bench",
        "--instances.subset",
        "lite",
        "--instances.split",
        "test",
        "--instances.filter",
        f"^{row['source_task_id']}$",
        "--instances.deployment.remove_images",
        "true",
        "--output_dir",
        str(arm_output_dir(run_root, row)),
        "--redo_existing",
        "true",
        "--num_workers",
        "1",
    ]
    args.extend(["--agent.model.per_instance_call_limit", str(per_instance_call_limit)])
    return args


def build_smoke_plan(
    *,
    selected_rows: list[dict[str, Any]],
    smoke_root: Path = DEFAULT_SMOKE_ROOT,
    provider: dict[str, Any] | None = None,
    swe_agent_config: Path = DEFAULT_SWE_AGENT_CONFIG,
    runner_wrapper: Path = MODELS / "run_phase315_one_pair_smoke.py",
) -> list[dict[str, Any]]:
    provider = provider or provider_summary(acknowledge_external_export=True)
    rows = []
    for row in selected_rows:
        runner_args = runner_args_for_arm(
            row=row,
            run_root=smoke_root,
            swe_agent_config=swe_agent_config,
            provider_model=provider["declared_model"],
        )
        rows.append(
            {
                "phase": "3.15",
                "smoke_version": PHASE315_SMOKE_VERSION,
                "execution_index": row["execution_index"],
                "pair_id": row["pair_id"],
                "arm_id": row["arm_id"],
                "arm_type": row["arm_type"],
                "source_task_id": row["source_task_id"],
                "source_family": row["source_family"],
                "candidate_prefix_index": row["candidate_prefix_index"],
                "candidate_prefix_sha256": row["candidate_prefix_sha256"],
                "intervention_policy_id": row["intervention_policy_id"],
                "inject_policy_message": row["inject_policy_message"],
                "injection_trigger_after_prefix_index": row["injection_trigger_after_prefix_index"],
                "policy_message_template_id": row["policy_message_template_id"],
                "policy_message_template_sha256": row["policy_message_template_sha256"],
                "provider_model": provider["declared_model"],
                "provider_config_source": provider["provider_config_source"],
                "provider_config_source_sha256": provider["provider_config_source_sha256"],
                "provider_secret_material_stored": False,
                "smoke_per_instance_call_limit": DEFAULT_SMOKE_PER_INSTANCE_CALL_LIMIT,
                "swe_agent_config": relative(swe_agent_config),
                "swe_agent_config_sha256": sha256_file(swe_agent_config),
                "smoke_output_dir": relative(arm_output_dir(smoke_root, row)),
                "runner_arg_sha256": stable_json_hash(runner_args),
                "runner_wrapper": relative(runner_wrapper),
                "real_run_started": False,
                "runner_started": False,
                "model_call_started": False,
                "raw_payload_persistence": "forbidden",
                "execution_status": "one_pair_smoke_plan_only_not_started",
            }
        )
    return rows


def call_limit_policy(per_arm_model_call_limit: int) -> str:
    return (
        "uncapped_no_call_count_limit"
        if per_arm_model_call_limit == 0
        else "wrapper_call_count_limit"
    )


def trigger_reachable_under_call_limit(row: dict[str, Any], per_arm_model_call_limit: int) -> bool:
    if row.get("arm_type") != "intervention":
        return False
    if per_arm_model_call_limit == 0:
        return True
    return int(row["candidate_prefix_index"]) <= per_arm_model_call_limit


def with_per_arm_call_limit(
    rows: list[dict[str, Any]], per_arm_model_call_limit: int
) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        copied = dict(row)
        copied["scheduled_per_arm_model_call_limit"] = row.get("per_arm_model_call_limit")
        copied["per_arm_model_call_limit"] = per_arm_model_call_limit
        copied["per_arm_call_limit_policy"] = call_limit_policy(per_arm_model_call_limit)
        normalized.append(copied)
    return normalized


def rows_by_arm_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["arm_id"]): row for row in rows}


def select_batch_rows(schedule: list[dict[str, Any]], batch_index: int) -> list[dict[str, Any]]:
    return sorted(
        [row for row in schedule if int(row.get("execution_batch_index", -1)) == batch_index],
        key=lambda row: int(row["execution_index"]),
    )


def selected_bridge_rows_for_batch(
    *,
    bridge_plan: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bridge_by_id = rows_by_arm_id(bridge_plan)
    return [
        bridge_by_id[str(row["arm_id"])]
        for row in selected_rows
        if str(row["arm_id"]) in bridge_by_id
    ]


def batch_root(run_root: Path, batch_index: int) -> Path:
    return run_root / f"batch_{batch_index:02d}"


def build_batch_plan(
    *,
    selected_rows: list[dict[str, Any]],
    selected_bridge_rows: list[dict[str, Any]],
    provider: dict[str, Any] | None = None,
    swe_agent_config: Path = DEFAULT_SWE_AGENT_CONFIG,
    run_root: Path = DEFAULT_BATCH_RUN_ROOT,
    batch_index: int = 1,
    per_arm_model_call_limit: int = DEFAULT_BATCH_PER_ARM_CALL_LIMIT,
) -> list[dict[str, Any]]:
    provider = provider or provider_summary(acknowledge_external_export=True)
    bridge_by_id = rows_by_arm_id(selected_bridge_rows)
    root = batch_root(run_root, batch_index)
    rows = []
    for row in selected_rows:
        bridge_row = bridge_by_id[str(row["arm_id"])]
        runner_args = runner_args_for_arm(
            row=bridge_row,
            run_root=root,
            swe_agent_config=swe_agent_config,
            provider_model=provider["declared_model"],
            per_instance_call_limit=per_arm_model_call_limit,
        )
        rows.append(
            {
                "phase": "3.16",
                "batch_version": PHASE316_BATCH_VERSION,
                "batch_index": batch_index,
                "execution_index": row["execution_index"],
                "execution_batch_index": row["execution_batch_index"],
                "pair_id": row["pair_id"],
                "arm_id": row["arm_id"],
                "arm_type": row["arm_type"],
                "source_task_id": row["source_task_id"],
                "source_family": row["source_family"],
                "candidate_prefix_index": row["candidate_prefix_index"],
                "candidate_prefix_sha256": row["candidate_prefix_sha256"],
                "intervention_policy_id": row["intervention_policy_id"],
                "declared_efe_mode": row["declared_efe_mode"],
                "resumable_state_key_sha256": row["resumable_state_key_sha256"],
                "scheduled_per_arm_model_call_limit": row.get("scheduled_per_arm_model_call_limit"),
                "per_arm_model_call_limit": per_arm_model_call_limit,
                "per_arm_call_limit_policy": call_limit_policy(per_arm_model_call_limit),
                "expected_trigger_reachable_under_call_cap": trigger_reachable_under_call_limit(
                    row,
                    per_arm_model_call_limit,
                ),
                "requires_trigger_telemetry": row["requires_trigger_telemetry"],
                "trigger_hit_classification": row["trigger_hit_classification"],
                "treatment_attribution_rule": row["treatment_attribution_rule"],
                "policy_bridge_mode": bridge_row["policy_bridge_mode"],
                "injection_trigger_after_prefix_index": bridge_row[
                    "injection_trigger_after_prefix_index"
                ],
                "policy_message_template_id": bridge_row.get("policy_message_template_id"),
                "policy_message_template_sha256": bridge_row.get("policy_message_template_sha256"),
                "provider_model": provider["declared_model"],
                "provider_config_source": provider["provider_config_source"],
                "provider_config_source_sha256": provider["provider_config_source_sha256"],
                "provider_secret_material_stored": False,
                "swe_agent_config": relative(swe_agent_config),
                "swe_agent_config_sha256": sha256_file(swe_agent_config),
                "batch_output_dir": relative(arm_output_dir(root, bridge_row)),
                "runner_arg_sha256": stable_json_hash(runner_args),
                "official_eval_status": "pending_after_arm_patch",
                "official_eval_started": False,
                "real_run_started": False,
                "runner_started": False,
                "model_call_started": False,
                "raw_payload_persistence": "forbidden",
                "execution_status": "phase316_batch_plan_only_not_started",
            }
        )
    return rows


def completed_resumable_state_keys(events: list[dict[str, Any]]) -> set[str]:
    keys = set()
    for event in events:
        if event.get("execution_status") in {"completed", "arm_completed", "succeeded"}:
            value = event.get("resumable_state_key_sha256") or event.get("resumable_state_key")
            if value:
                keys.add(str(value))
    return keys


def filter_pending_arms(
    rows: list[dict[str, Any]], completed_keys: set[str]
) -> list[dict[str, Any]]:
    pending = []
    for row in rows:
        key = row.get("resumable_state_key_sha256") or row.get("resumable_state_key")
        if key and str(key) in completed_keys:
            continue
        pending.append(row)
    return pending


def is_raw_generated_artifact(path: Path) -> bool:
    if path.suffix in RAW_GENERATED_SUFFIXES:
        return True
    if path.name in RAW_GENERATED_NAMES:
        return True
    return any(path.name.endswith(suffix) for suffix in RAW_GENERATED_NAME_SUFFIXES)


def scrub_raw_generated_artifacts(root: Path, *, phase: str = "3.15") -> list[dict[str, Any]]:
    if not root.exists():
        return []
    audit_rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_raw_generated_artifact(path):
            continue
        audit_rows.append(
            {
                "phase": phase,
                "artifact_path": relative(path),
                "artifact_sha256": sha256_file(path),
                "artifact_size_bytes": path.stat().st_size,
                "artifact_class": "raw_sweagent_runtime_artifact",
                "privacy_action": "sha256_audited_then_removed",
            }
        )
        path.unlink()
    return audit_rows


def remaining_raw_generated_artifacts(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [
        relative(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and is_raw_generated_artifact(path)
    ]


def pair_rows_from_pairs(pairs: list[InterventionPair]) -> list[dict[str, Any]]:
    rows = []
    for pair in pairs:
        rows.append(pair.control.to_dict())
        rows.append(pair.intervention.to_dict())
    return rows


def _result_for_pair(
    pair_id: str, attribution: str, *, skipped: bool = False
) -> InterventionResult:
    return InterventionResult(
        pair_id=pair_id, attribution=attribution, metadata={"skipped": skipped}
    )


def run_batch(
    pairs: list[InterventionPair],
    provider: object | None = None,
    hook_factory: object | None = None,
    call_limit: int = 0,
    ack_external: bool = False,
    completed_state_keys: set[str] | None = None,
) -> list[InterventionResult]:
    if provider is not None and not ack_external:
        raise ValueError("ack_external is required before calling an external provider")
    completed_state_keys = completed_state_keys or set()
    rows_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows_from_pairs(pairs):
        key = row.get("resumable_state_key_sha256") or row.get("resumable_state_key")
        if key and str(key) in completed_state_keys:
            continue
        rows_by_pair[str(row["pair_id"])].append(row)
    results = []
    for pair in pairs:
        if not rows_by_pair.get(pair.pair_id):
            results.append(
                _result_for_pair(pair.pair_id, "skipped_completed_resumable_state", skipped=True)
            )
        else:
            results.append(_result_for_pair(pair.pair_id, "dry_run_not_executed"))
    return results
