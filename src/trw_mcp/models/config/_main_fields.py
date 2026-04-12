"""TRWConfig field declarations — assembled from domain mixin files.

Split from a 468-line monolith (PRD-CORE-089-FR01) into 8 domain-specific
mixin files. This module is the thin assembly shell that composes them
into a single BaseSettings class via multiple inheritance.

TRWConfig in _main.py inherits from _TRWConfigFields and adds
@cached_property facades, helper methods, and client profile resolution.

All field declarations live in the _fields_*.py domain files.
Both application code and tests import TRWConfig from _main.py —
this module is internal.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from trw_mcp.models.config._fields_bandit import _BanditFields
from trw_mcp.models.config._fields_build import _BuildFields
from trw_mcp.models.config._fields_ceremony import _CeremonyFields
from trw_mcp.models.config._fields_memory import _MemoryFields
from trw_mcp.models.config._fields_orchestration import _OrchestrationFields
from trw_mcp.models.config._fields_paths import _PathsFields
from trw_mcp.models.config._fields_scoring import _ScoringFields
from trw_mcp.models.config._fields_sync import _SyncFields
from trw_mcp.models.config._fields_telemetry import _TelemetryFields
from trw_mcp.models.config._fields_tools import _ToolsFields
from trw_mcp.models.config._fields_trust import _TrustFields


class _TRWConfigFields(
    _ScoringFields,
    _MemoryFields,
    _OrchestrationFields,
    _TelemetryFields,
    _CeremonyFields,
    _BanditFields,
    _BuildFields,
    _ToolsFields,
    _TrustFields,
    _SyncFields,
    _PathsFields,
    BaseSettings,
):
    """All TRW configuration fields.

    Values come from (in priority order):
    1. Environment variables (prefixed TRW_)
    2. .trw/config.yaml overrides (loaded at runtime)
    3. Defaults defined in domain mixin files (from FRAMEWORK.md DEFAULTS)

    Unknown environment variables and config.yaml keys are silently ignored.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRW_",
        case_sensitive=False,
        extra="ignore",
    )
