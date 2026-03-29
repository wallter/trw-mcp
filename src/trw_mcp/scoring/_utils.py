"""Shared scoring constants, state, and re-exports from trw_memory.

Internal module — all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

import math

import structlog
from trw_memory.lifecycle.scoring import (
    _clamp01 as _clamp01,
)
from trw_memory.lifecycle.scoring import (
    _ensure_utc as _ensure_utc,
)
from trw_memory.lifecycle.scoring import (
    apply_time_decay as apply_time_decay,
)
from trw_memory.lifecycle.scoring import (
    bayesian_calibrate as bayesian_calibrate,
)
from trw_memory.lifecycle.scoring import (
    compute_calibration_accuracy as compute_calibration_accuracy,
)
from trw_memory.lifecycle.scoring import (
    compute_utility_score as compute_utility_score,
)
from trw_memory.lifecycle.scoring import (
    update_q_value as update_q_value,
)

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._helpers import safe_float, safe_int
from trw_mcp.state._paths import resolve_trw_dir as resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


# --- Scoring constants ---

_LN2: float = math.log(2)  # ~0.693 -- Ebbinghaus decay exponent
_IMPACT_DECAY_FLOOR: float = 0.1  # Minimum impact after exponential decay

# Tier boundary thresholds for enforce_tier_distribution
_TIER_HIGH_CEILING: float = 0.89  # Top of high tier (demotion target)
_TIER_MEDIUM_CEILING: float = 0.69  # Top of medium tier (demotion target)

__all__ = [
    "_IMPACT_DECAY_FLOOR",
    "_LN2",
    "_TIER_HIGH_CEILING",
    "_TIER_MEDIUM_CEILING",
    "TRWConfig",
    "_clamp01",
    "_ensure_utc",
    "apply_time_decay",
    "bayesian_calibrate",
    "compute_calibration_accuracy",
    "compute_utility_score",
    "get_config",
    "logger",
    "safe_float",
    "safe_int",
    "update_q_value",
]
# NOTE: FileStateReader, FileStateWriter, and resolve_trw_dir are still
# importable from this module (used by sibling scoring sub-modules), but
# are deliberately excluded from __all__ because they are state-layer I/O
# primitives that should not be part of the scoring public API.
# PRD-FIX-061-FR03.
