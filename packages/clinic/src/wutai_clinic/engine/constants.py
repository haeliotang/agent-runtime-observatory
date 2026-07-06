from __future__ import annotations

import math

BFT_F = 1.0 / 3.0
BFT_VARIANCE_PRIOR = BFT_F * (1.0 - BFT_F)
TOPOLOGICAL_IMPEDANCE = 6561.0
BOOTSTRAP_MIN_SAMPLES = int(math.ceil(math.log(3**4)))
