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
from trw_mcp.models.config._loader import (
    _reset_config,
    get_config,
    reload_config,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._model_capabilities import lookup_model_effort_capabilities, match_model_family
from trw_mcp.models.config._profiles import (
    builtin_client_ids,
    resolve_client_profile,
)
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    DispatchConfig,
    IntentContractConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    SecurityConfig,
    TelemetryConfig,
    ToolsConfig,
    TrustConfig,
)
from trw_mcp.models.config._unread_fields import unread_config_fields

__all__ = [
    "BuildConfig",
    "CapabilityTier",
    "CeremonyFeedbackConfig",
    "CeremonyWeights",
    "ClientProfile",
    "DispatchConfig",
    "IntentContractConfig",
    "LegacyModelTier",
    "MemoryConfig",
    "ModelTier",
    "NudgePoolWeights",
    "OrchestrationConfig",
    "PathsConfig",
    "ScoringConfig",
    "ScoringDimensionWeights",
    "SecurityConfig",
    "TRWConfig",
    "TelemetryConfig",
    "ToolsConfig",
    "TrustConfig",
    "WriteTargets",
    "_reset_config",
    "builtin_client_ids",
    "get_config",
    "lookup_model_effort_capabilities",
    "match_model_family",
    "normalize_capability_tier",
    "reload_config",
    "resolve_client_profile",
    "unread_config_fields",
]
