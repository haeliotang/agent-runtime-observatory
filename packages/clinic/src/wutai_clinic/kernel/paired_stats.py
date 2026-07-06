"""Paired-outcome statistics, re-exported under a runtime-agnostic name.

Implementations live in ``engine.power`` and ``engine.epsilon`` (stdlib +
numpy only; nothing SWE-bench-specific). External harnesses should import
from here — the engine paths are clinic-internal and may move.
"""

from wutai_clinic.engine.epsilon import (
    flip_rate_estimate,
    required_pairs_with_noise,
    wilson_interval,
)
from wutai_clinic.engine.power import (
    discordant_pair_test,
    exact_binomial_tail,
    futility_boundary,
    max_effect_excluded,
    required_pairs,
)

__all__ = [
    "discordant_pair_test",
    "exact_binomial_tail",
    "flip_rate_estimate",
    "futility_boundary",
    "max_effect_excluded",
    "required_pairs",
    "required_pairs_with_noise",
    "wilson_interval",
]
