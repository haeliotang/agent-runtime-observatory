from __future__ import annotations

from .attribution import attribute
from .hooks import (
    LiveFeatureHook,
    Phase315PolicyInjectionHook,
    Phase316LiveFeaturePolicyHook,
    StaticPrefixHook,
)
from .hybrid_runner import (
    CapsuleBuildContext,
    CapsuleMaterializationHook,
    HybridReplayGenerationModel,
    message_prefix_hash,
)
from .paired_fork import (
    PAIRED_FORK_DRY_RUN_VERSION,
    default_protocol,
    run_paired_fork_dry_run,
)
from .planner import plan
from .protocol_v1 import (
    ProtocolV1,
    build_protocol_v1_plan,
    protocol_v1_for_no_uplift_classification,
)
from .protocol_v1_batch_outcomes import write_protocol_v1_batch_outcomes_evidence
from .protocol_v2_batch_outcomes import write_protocol_v2_batch_outcomes_evidence
from .protocol_v1_dry_run import write_protocol_v1_dry_run_evidence
from .protocol_v1_hook import ProtocolV1ConstraintHook, ProtocolV1ConstraintViolation
from .protocol_v1_hook_preflight import write_protocol_v1_hook_preflight_evidence
from .protocol_v1_fresh_candidates import write_protocol_v1_fresh_candidate_evidence
from .protocol_v2 import ProtocolV2, protocol_v2_prescription_template
from .protocol_v2_dry_run import write_protocol_v2_dry_run_evidence
from .protocol_v2_fresh_candidates import write_protocol_v2_fresh_candidate_evidence
from .protocol_v2_hook import ProtocolV2ConstraintHook, ProtocolV2ConstraintViolation
from .protocol_v2_pair_inputs import write_protocol_v2_pair_inputs_evidence
from .protocol_v2_planned_preflight import write_protocol_v2_planned_preflight_evidence
from .fresh_target_harvest import (
    CLAIM_BOUNDARY as FRESH_HARVEST_CLAIM_BOUNDARY,
    FRESH_HARVEST_PHASE,
    run_fresh_target_harvest,
    write_fresh_target_harvest_plan,
)
from .oracle_capsule import (
    CLAIM_BOUNDARY as ORACLE_PROBE_CLAIM_BOUNDARY,
    ORACLE_PROBE_LAYER,
    build_oracle_probe_runtime_config,
    distill_gold_to_capsule,
    load_oracle_probe_rows,
    write_oracle_probe_outcome_evidence,
    write_oracle_probe_prepare_evidence,
)
from .replay_protocol import (
    InterventionProtocol,
    StateCapsule,
    paired_replay_effect_label,
    protocol_check_report,
    simulate_protocol,
    verify_fork_equivalence,
)

__all__ = [
    "CapsuleBuildContext",
    "CapsuleMaterializationHook",
    "HybridReplayGenerationModel",
    "InterventionProtocol",
    "LiveFeatureHook",
    "PAIRED_FORK_DRY_RUN_VERSION",
    "Phase315PolicyInjectionHook",
    "Phase316LiveFeaturePolicyHook",
    "ProtocolV1",
    "ProtocolV1ConstraintHook",
    "ProtocolV1ConstraintViolation",
    "ProtocolV2",
    "ProtocolV2ConstraintHook",
    "ProtocolV2ConstraintViolation",
    "StateCapsule",
    "StaticPrefixHook",
    "attribute",
    "build_protocol_v1_plan",
    "default_protocol",
    "message_prefix_hash",
    "paired_replay_effect_label",
    "plan",
    "protocol_v1_for_no_uplift_classification",
    "protocol_v2_prescription_template",
    "protocol_check_report",
    "run_paired_fork_dry_run",
    "simulate_protocol",
    "verify_fork_equivalence",
    "write_protocol_v1_dry_run_evidence",
    "write_protocol_v1_batch_outcomes_evidence",
    "write_protocol_v2_batch_outcomes_evidence",
    "write_protocol_v1_fresh_candidate_evidence",
    "write_protocol_v1_hook_preflight_evidence",
    "write_protocol_v2_dry_run_evidence",
    "write_protocol_v2_fresh_candidate_evidence",
    "write_protocol_v2_pair_inputs_evidence",
    "write_protocol_v2_planned_preflight_evidence",
    "FRESH_HARVEST_CLAIM_BOUNDARY",
    "FRESH_HARVEST_PHASE",
    "run_fresh_target_harvest",
    "write_fresh_target_harvest_plan",
    "ORACLE_PROBE_CLAIM_BOUNDARY",
    "ORACLE_PROBE_LAYER",
    "build_oracle_probe_runtime_config",
    "distill_gold_to_capsule",
    "load_oracle_probe_rows",
    "write_oracle_probe_outcome_evidence",
    "write_oracle_probe_prepare_evidence",
]
