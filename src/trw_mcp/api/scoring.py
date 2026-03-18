"""Public scoring API -- stable interface for trw-eval and external consumers.

INVARIANT: Importing this module MUST NOT trigger TRWConfig() instantiation.
All re-exports are lazy or from modules that do not instantiate singletons.
Verified by test_api_import_no_side_effects().

Stability: Additions are minor version bumps. Removals are major version bumps.
"""

from __future__ import annotations

# Models -- no side effects on import (frozen Pydantic models)
from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    ModelTier,
    ScoringDimensionWeights,
    WriteTargets,
)
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.models.learning import LearningEntry
from trw_mcp.models.typed_dicts._ceremony import CeremonyScoreResult

# Functions -- no side effects on import (defined in module scope, not called)
from trw_mcp.state.analytics.report import compute_ceremony_score
from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

# Public constant -- replaces _CEREMONY_WEIGHTS (F02/FR02)
CEREMONY_WEIGHTS = CeremonyWeights()  # frozen, defaults = production values

__all__ = [
    "CEREMONY_WEIGHTS",
    "CeremonyScoreResult",
    "CeremonyWeights",
    "ClientProfile",
    "LearningEntry",
    "ModelTier",
    "ScoringDimensionWeights",
    "WriteTargets",
    "compute_ceremony_score",
    "resolve_client_profile",
    "validate_prd_quality_v2",
]
