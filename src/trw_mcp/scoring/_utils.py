"""Shared scoring constants, state, and re-exports from trw_memory.

Internal module — all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

import math

import structlog

from trw_memory.lifecycle.scoring import (
    _clamp01 as _clamp01,
    _ensure_utc as _ensure_utc,
    apply_time_decay as apply_time_decay,
    bayesian_calibrate as bayesian_calibrate,
    compute_calibration_accuracy as compute_calibration_accuracy,
    compute_utility_score as compute_utility_score,
    update_q_value as update_q_value,
)

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._helpers import safe_float, safe_int
from trw_mcp.state._paths import resolve_trw_dir as resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()

# --- Scoring constants ---

_LN2: float = math.log(2)  # ~0.693 -- Ebbinghaus decay exponent
_IMPACT_DECAY_FLOOR: float = 0.1  # Minimum impact after exponential decay

# Tier boundary thresholds for enforce_tier_distribution
_TIER_HIGH_CEILING: float = 0.89  # Top of high tier (demotion target)
_TIER_MEDIUM_CEILING: float = 0.69  # Top of medium tier (demotion target)

__all__ = [
    # Re-exports from trw_memory
    "_clamp01",
    "_ensure_utc",
    "apply_time_decay",
    "bayesian_calibrate",
    "compute_calibration_accuracy",
    "compute_utility_score",
    "update_q_value",
    # Shared state
    "logger",
    "_config",
    "_reader",
    "_writer",
    # Constants
    "_LN2",
    "_IMPACT_DECAY_FLOOR",
    "_TIER_HIGH_CEILING",
    "_TIER_MEDIUM_CEILING",
    # Re-exported helpers
    "safe_float",
    "safe_int",
    "get_config",
    "TRWConfig",
]
