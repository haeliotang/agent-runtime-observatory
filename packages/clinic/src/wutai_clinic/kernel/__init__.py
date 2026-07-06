"""Runtime-agnostic methodology kernel.

The clinic's reusable core, deliberately free of SWE-bench / SWE-agent / any
specific runtime: paired-outcome statistics, manipulation-check gating, and
hash-verified agent-state snapshots. The cognition-ablation line (TS runtime)
re-implemented these by hand; this package is the canonical home so the next
runtime imports instead of rewriting.

Modules:
- paired_stats         — McNemar exact, power/futility, Wilson, flip rate
- manipulation_checks  — declarative M-check specs evaluated over arm records
- state_snapshot       — byte-faithful snapshot/verify/restore of agent state
"""

from wutai_clinic.kernel.manipulation_checks import (
    ManipulationCheck,
    evaluate_manipulation_checks,
)
from wutai_clinic.kernel.paired_stats import (
    discordant_pair_test,
    flip_rate_estimate,
    futility_boundary,
    max_effect_excluded,
    required_pairs,
    required_pairs_with_noise,
    wilson_interval,
)
from wutai_clinic.kernel.state_snapshot import (
    restore_snapshot,
    take_snapshot,
    verify_snapshot,
)

__all__ = [
    "ManipulationCheck",
    "discordant_pair_test",
    "evaluate_manipulation_checks",
    "flip_rate_estimate",
    "futility_boundary",
    "max_effect_excluded",
    "required_pairs",
    "required_pairs_with_noise",
    "restore_snapshot",
    "take_snapshot",
    "verify_snapshot",
    "wilson_interval",
]
