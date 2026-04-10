"""Unified surface configuration -- resolved from profile + flat fields.

Provides typed access to all surface control flags via a single
frozen model, eliminating scattered getattr calls at gate sites.

PRD-CORE-125 Phase 3 (FR13): SurfaceConfig is built by TRWConfig.surfaces
cached_property, which resolves profile defaults against flat overrides.
"""

from __future__ import annotations

from typing import Literal

# Note: ToolExposureConfig.mode is typed as ``str`` rather than
# ``Literal["all", "core", "minimal", "standard", "custom"]`` because
# the Literal validation already happens at the input boundary
# (_fields_tools.py).  SurfaceConfig is a resolved snapshot, not an
# input model, so repeating the Literal constraint here would require
# a ``cast(Any, ...)`` in _main.py to satisfy mypy.

from pydantic import BaseModel, ConfigDict, Field


class NudgeConfig(BaseModel):
    """Nudge surface configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    urgency_mode: Literal["adaptive", "always_low", "always_high", "off"] = "adaptive"
    budget_chars: int = Field(default=600, ge=100, le=2000)
    dedup_enabled: bool = True


class ToolExposureConfig(BaseModel):
    """Tool exposure surface configuration."""

    model_config = ConfigDict(frozen=True)

    mode: str = "all"
    custom_list: tuple[str, ...] = ()


class RecallConfig(BaseModel):
    """Learning recall surface configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_results: int = Field(default=25, ge=1)
    injection_preview_chars: int = Field(default=500, ge=50, le=2000)
    session_start_recall: bool = True


class SurfaceConfig(BaseModel):
    """Unified surface configuration.

    Built by ``TRWConfig.surfaces`` from profile defaults + flat field
    overrides.  All gate sites read from this frozen model rather than
    performing their own profile resolution.
    """

    model_config = ConfigDict(frozen=True)

    nudge: NudgeConfig = Field(default_factory=NudgeConfig)
    tool_exposure: ToolExposureConfig = Field(default_factory=ToolExposureConfig)
    recall: RecallConfig = Field(default_factory=RecallConfig)
    mcp_instructions_enabled: bool = True
    hooks_enabled: bool = True
    skills_enabled: bool = True
    agents_enabled: bool = True
    framework_ref_enabled: bool = True
    tool_descriptions_variant: Literal["default", "minimal", "verbose"] = "default"
