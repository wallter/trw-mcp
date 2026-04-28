"""Typed TaskProfile models shared by run metadata and resolver code."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskArchetype = Literal["bugfix", "feature", "docs", "refactor", "audit", "research", "unknown"]
ComplexityClassName = Literal["MINIMAL", "STANDARD", "COMPREHENSIVE"]
NudgePolicy = Literal["off", "sparse", "standard", "dense"]
TraceDepth = Literal["minimal", "standard", "causal"]
CeremonyDepth = Literal["light", "standard", "comprehensive"]
ToolExposurePreset = Literal["all", "core", "minimal", "standard", "custom"]


class TaskProfileOverrides(BaseModel):
    """Optional explicit overrides for task-profile resolution."""

    model_config = ConfigDict(frozen=True)

    ceremony_depth: CeremonyDepth | None = None
    mandatory_phases: tuple[str, ...] | None = None
    exposed_tool_preset: ToolExposurePreset | None = None
    nudge_policy: NudgePolicy | None = None
    trace_depth: TraceDepth | None = None
    instruction_budget_lines: int | None = Field(default=None, ge=1)
    context_window_tokens: int | None = Field(default=None, ge=1)


class TaskProfile(BaseModel):
    """Resolved operating profile for one concrete task/run."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    model_tier: str
    complexity_class: ComplexityClassName
    task_archetype: TaskArchetype = "unknown"
    ceremony_depth: CeremonyDepth
    mandatory_phases: tuple[str, ...] = Field(default_factory=tuple)
    exposed_tool_preset: ToolExposurePreset
    nudge_policy: NudgePolicy
    trace_depth: TraceDepth
    instruction_budget_lines: int
    context_window_tokens: int
    rationale: tuple[str, ...] = Field(default_factory=tuple)
    profile_hash: str

    @property
    def client_id(self) -> str:
        """Backward-compatible alias for the source client profile ID."""
        return self.profile_id
