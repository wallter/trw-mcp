"""Framework configuration package -- re-exports all public names.

All existing imports of the form ``from trw_mcp.models.config import X``
continue to work unchanged.
"""

from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    ModelTier,
    ScoringDimensionWeights,
    WriteTargets,
)
from trw_mcp.models.config._loader import (
    _reset_config,
    get_config,
    reload_config,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    PhaseTimeCaps,
    ScoringConfig,
    TelemetryConfig,
    ToolsConfig,
    TrustConfig,
)
from trw_mcp.models.config._surface_config import (
    NudgeConfig,
    RecallConfig,
    SurfaceConfig,
    ToolExposureConfig,
)

__all__ = [
    "BuildConfig",
    "CeremonyFeedbackConfig",
    "CeremonyWeights",
    "ClientProfile",
    "MemoryConfig",
    "ModelTier",
    "NudgeConfig",
    "OrchestrationConfig",
    "PathsConfig",
    "PhaseTimeCaps",
    "RecallConfig",
    "ScoringConfig",
    "ScoringDimensionWeights",
    "SurfaceConfig",
    "TRWConfig",
    "TelemetryConfig",
    "ToolExposureConfig",
    "ToolsConfig",
    "TrustConfig",
    "WriteTargets",
    "_reset_config",
    "get_config",
    "reload_config",
    "resolve_client_profile",
]
