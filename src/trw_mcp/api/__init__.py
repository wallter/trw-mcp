"""Public API -- stable import paths for cross-package consumers.

This module provides a curated subset of trw-mcp types for downstream
consumers. Internal code should import from the full module paths.
"""

# --- Core configuration ---
from trw_mcp.models.config import TRWConfig as TRWConfig
from trw_mcp.models.config import get_config as get_config

# --- Learning models ---
from trw_mcp.models.learning import LearningEntry as LearningEntry
from trw_mcp.models.learning import LearningStatus as LearningStatus

# --- Run state models ---
from trw_mcp.models.run import Event as Event
from trw_mcp.models.run import Phase as Phase
from trw_mcp.models.run import RunState as RunState

# --- Requirements models ---
from trw_mcp.models.requirements import ValidationResult as ValidationResult

# --- Exceptions ---
from trw_mcp.exceptions import StateError as StateError
from trw_mcp.exceptions import TRWError as TRWError

# --- Scoring API (re-exported from api.scoring) ---
from trw_mcp.api.scoring import (
    CEREMONY_WEIGHTS as CEREMONY_WEIGHTS,
    CeremonyScoreResult as CeremonyScoreResult,
    CeremonyWeights as CeremonyWeights,
    ClientProfile as ClientProfile,
    ModelTier as ModelTier,
    ScoringDimensionWeights as ScoringDimensionWeights,
    WriteTargets as WriteTargets,
    compute_ceremony_score as compute_ceremony_score,
    resolve_client_profile as resolve_client_profile,
    validate_prd_quality_v2 as validate_prd_quality_v2,
)

__all__ = [
    # FR01: Core public types
    "TRWConfig",
    "get_config",
    "LearningEntry",
    "LearningStatus",
    "Phase",
    "RunState",
    "Event",
    "ValidationResult",
    "TRWError",
    "StateError",
    # Scoring API
    "CEREMONY_WEIGHTS",
    "CeremonyScoreResult",
    "CeremonyWeights",
    "ClientProfile",
    "ModelTier",
    "ScoringDimensionWeights",
    "WriteTargets",
    "compute_ceremony_score",
    "resolve_client_profile",
    "validate_prd_quality_v2",
]
