"""Framework configuration package -- re-exports all public names.

All existing imports of the form ``from trw_mcp.models.config import X``
continue to work unchanged.
"""

from trw_mcp.models.config._capability import (
    CapabilityTier,
    LegacyModelTier,
    ModelTier,
    normalize_capability_tier,
)
from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    NudgePoolWeights,
    ScoringDimensionWeights,
    WriteTargets,
)
from trw_mcp.models.config._execution_effort import EffortAdapterDecision, resolve_effort_adapter
from trw_mcp.models.config._loader import (
    _reset_config,
    get_config,
    reload_config,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._model_capabilities import (
    ANTHROPIC_MODEL_CATALOG_VERSION,
    lookup_model_effort_capabilities,
)
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    DispatchConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    PhaseTimeCaps,
    ScoringConfig,
    SecurityConfig,
    TelemetryConfig,
    ToolsConfig,
    TrustConfig,
)
from trw_mcp.models.config._surface_config import (
    NudgeConfig,
    RecallConfig,
    SurfaceConfig,
)

__all__ = [
    "ANTHROPIC_MODEL_CATALOG_VERSION",
    "BuildConfig",
    "CapabilityTier",
    "CeremonyFeedbackConfig",
    "CeremonyWeights",
    "ClientProfile",
    "DispatchConfig",
    "EffortAdapterDecision",
    "LegacyModelTier",
    "MemoryConfig",
    "ModelTier",
    "NudgeConfig",
    "NudgePoolWeights",
    "OrchestrationConfig",
    "PathsConfig",
    "PhaseTimeCaps",
    "RecallConfig",
    "ScoringConfig",
    "ScoringDimensionWeights",
    "SecurityConfig",
    "SurfaceConfig",
    "TRWConfig",
    "TelemetryConfig",
    "ToolsConfig",
    "TrustConfig",
    "WriteTargets",
    "_reset_config",
    "get_config",
    "lookup_model_effort_capabilities",
    "normalize_capability_tier",
    "reload_config",
    "resolve_client_profile",
    "resolve_effort_adapter",
]
