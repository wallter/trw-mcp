"""Framework configuration package -- re-exports all public names.

All existing imports of the form ``from trw_mcp.models.config import X``
continue to work unchanged.
"""

from trw_mcp.models.config._loader import _reset_config, get_config
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    PhaseTimeCaps,
    ScoringConfig,
    TelemetryConfig,
    TrustConfig,
)

__all__ = [
    "BuildConfig",
    "CeremonyFeedbackConfig",
    "MemoryConfig",
    "OrchestrationConfig",
    "PathsConfig",
    "PhaseTimeCaps",
    "ScoringConfig",
    "TRWConfig",
    "TelemetryConfig",
    "TrustConfig",
    "_reset_config",
    "get_config",
]
