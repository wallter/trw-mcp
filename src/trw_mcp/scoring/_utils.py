"""Shared scoring constants, state, and re-exports from trw_memory.

Internal module — all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

import structlog
from trw_memory.lifecycle.scoring import (
    _clamp01,
    _ensure_utc,
    apply_time_decay,
    compute_utility_score,
)

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._helpers import safe_float, safe_int
from trw_mcp.state._paths import resolve_trw_dir as resolve_trw_dir

logger = structlog.get_logger(__name__)


__all__ = [
    "TRWConfig",
    "_clamp01",
    "_ensure_utc",
    "apply_time_decay",
    "compute_utility_score",
    "get_config",
    "logger",
    "safe_float",
    "safe_int",
]
# NOTE: FileStateReader, FileStateWriter, and resolve_trw_dir are still
# importable from this module (used by sibling scoring sub-modules), but
# are deliberately excluded from __all__ because they are state-layer I/O
# primitives that should not be part of the scoring public API.
# PRD-FIX-061-FR03.
