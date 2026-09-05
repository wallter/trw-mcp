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
from trw_mcp.models.config._fields_boot_maintenance import _BootMaintenanceFields
from trw_mcp.models.config._fields_build import _BuildFields
from trw_mcp.models.config._fields_ceremony import _CeremonyFields
from trw_mcp.models.config._fields_degenerate_result import _DegenerateResultFields
from trw_mcp.models.config._fields_degraded_mode import _DegradedModeFields
from trw_mcp.models.config._fields_delivery import _DeliveryFields
from trw_mcp.models.config._fields_dispatch import _DispatchFields
from trw_mcp.models.config._fields_doctor_thread_hotspots import _DoctorThreadHotspotFields
from trw_mcp.models.config._fields_feedback import _FeedbackFields
from trw_mcp.models.config._fields_formation import _FormationFields
from trw_mcp.models.config._fields_instruction_surfaces import _InstructionSurfaceFields
from trw_mcp.models.config._fields_learn_journal import _LearnJournalFields
from trw_mcp.models.config._fields_memory import _MemoryFields
from trw_mcp.models.config._fields_memory_truth import _MemoryTruthFields
from trw_mcp.models.config._fields_nudge import _NudgeFields
from trw_mcp.models.config._fields_orchestration import _OrchestrationFields
from trw_mcp.models.config._fields_paths import _PathsFields
from trw_mcp.models.config._fields_phase_exposure import _PhaseExposureFields
from trw_mcp.models.config._fields_prd import _PRDFields
from trw_mcp.models.config._fields_profile import _ProfileFields
from trw_mcp.models.config._fields_scoring import _ScoringFields
from trw_mcp.models.config._fields_scout import _ScoutFields
from trw_mcp.models.config._fields_sync import _SyncFields
from trw_mcp.models.config._fields_telemetry import _TelemetryFields
from trw_mcp.models.config._fields_tools import _ToolsFields
from trw_mcp.models.config._fields_trust import _TrustFields
from trw_mcp.models.config._fields_verification import _VerificationFields


class _TRWConfigFields(
    _ScoringFields,
    _MemoryFields,
    _MemoryTruthFields,
    _LearnJournalFields,
    _OrchestrationFields,
    _TelemetryFields,
    _CeremonyFields,
    _DegenerateResultFields,
    _DegradedModeFields,
    _DeliveryFields,
    _FormationFields,
    _InstructionSurfaceFields,
    _FeedbackFields,
    _NudgeFields,
    _BanditFields,
    _BuildFields,
    _DispatchFields,
    _ToolsFields,
    _TrustFields,
    _SyncFields,
    _PathsFields,
    _PRDFields,
    _PhaseExposureFields,
    _ProfileFields,
    _ScoutFields,
    _VerificationFields,
    _BootMaintenanceFields,
    _DoctorThreadHotspotFields,
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
