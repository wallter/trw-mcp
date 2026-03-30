"""Public API -- stable import paths for cross-package consumers.

This module provides a curated subset of trw-mcp types for downstream
consumers. Internal code should import from the full module paths.
"""

# --- Core configuration ---
# --- Scoring API (re-exported from api.scoring) ---
from trw_mcp.api.scoring import (
    CEREMONY_WEIGHTS as CEREMONY_WEIGHTS,
)
from trw_mcp.api.scoring import (
    CeremonyScoreResult as CeremonyScoreResult,
)
from trw_mcp.api.scoring import (
    CeremonyWeights as CeremonyWeights,
)
from trw_mcp.api.scoring import (
    ClientProfile as ClientProfile,
)
from trw_mcp.api.scoring import (
    ModelTier as ModelTier,
)
from trw_mcp.api.scoring import (
    ScoringDimensionWeights as ScoringDimensionWeights,
)
from trw_mcp.api.scoring import (
    WriteTargets as WriteTargets,
)
from trw_mcp.api.scoring import (
    compute_ceremony_score as compute_ceremony_score,
)
from trw_mcp.api.scoring import (
    resolve_client_profile as resolve_client_profile,
)
from trw_mcp.api.scoring import (
    validate_prd_quality_v2 as validate_prd_quality_v2,
)

# --- Exceptions ---
from trw_mcp.exceptions import StateError as StateError
from trw_mcp.exceptions import TRWError as TRWError
from trw_mcp.models.config import TRWConfig as TRWConfig
from trw_mcp.models.config import get_config as get_config

# --- Learning models ---
from trw_mcp.models.learning import LearningEntry as LearningEntry
from trw_mcp.models.learning import LearningStatus as LearningStatus

# --- Requirements models ---
from trw_mcp.models.requirements import ValidationResult as ValidationResult

# --- Run state models ---
from trw_mcp.models.run import Event as Event
from trw_mcp.models.run import Phase as Phase
from trw_mcp.models.run import RunState as RunState

__all__ = [
    "CEREMONY_WEIGHTS",
    "CeremonyScoreResult",
    "CeremonyWeights",
    "ClientProfile",
    "Event",
    "LearningEntry",
    "LearningStatus",
    "ModelTier",
    "Phase",
    "RunState",
    "ScoringDimensionWeights",
    "StateError",
    "TRWConfig",
    "TRWError",
    "ValidationResult",
    "WriteTargets",
    "compute_ceremony_score",
    "get_config",
    "resolve_client_profile",
    "validate_prd_quality_v2",
]
