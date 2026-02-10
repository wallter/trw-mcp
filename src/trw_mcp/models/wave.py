"""Wave adaptation models — dynamic wave re-planning between waves.

PRD-CORE-006: Models for trigger evaluation, adaptation proposals,
and versioned manifest history.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AdaptationTriggerType(str, Enum):
    """Trigger types that can cause wave adaptation."""

    LOW_CONFIDENCE = "low_confidence"
    SHARD_SIGNAL = "shard_signal"
    VALIDATION_FAILURE = "validation_failure"
    SCOPE_EXPANSION = "scope_expansion"
    DEPENDENCY_CHANGE = "dependency_change"
    PARTIAL_COMPLETION = "partial_completion"
    NEW_REQUIREMENT = "new_requirement"
    RESOURCE_CONSTRAINT = "resource_constraint"


class AdaptationSeverity(str, Enum):
    """Severity of a proposed adaptation."""

    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class AdaptationAction(str, Enum):
    """Actions that can be taken as part of an adaptation."""

    ADD_SHARD = "add_shard"
    REMOVE_SHARD = "remove_shard"
    ADD_WAVE = "add_wave"
    REMOVE_WAVE = "remove_wave"
    MODIFY_SHARD = "modify_shard"
    REORDER_WAVES = "reorder_waves"


class AdaptationTrigger(BaseModel):
    """A detected trigger for wave adaptation."""

    model_config = ConfigDict(use_enum_values=True)

    trigger_type: AdaptationTriggerType
    source_shard: str = ""
    source_wave: int = 0
    description: str = ""
    severity: AdaptationSeverity = AdaptationSeverity.MINOR
    evidence: dict[str, object] = Field(default_factory=dict)


class ProposedChange(BaseModel):
    """A single proposed change to the wave manifest."""

    model_config = ConfigDict(use_enum_values=True)

    action: AdaptationAction
    target_wave: int = 0
    target_shard: str = ""
    description: str = ""
    shard_definition: dict[str, object] = Field(default_factory=dict)


class AdaptationProposal(BaseModel):
    """A complete adaptation proposal with triggers, changes, and severity."""

    model_config = ConfigDict(use_enum_values=True)

    triggers: list[AdaptationTrigger] = Field(default_factory=list)
    changes: list[ProposedChange] = Field(default_factory=list)
    severity: AdaptationSeverity = AdaptationSeverity.MINOR
    rationale: str = ""
    shards_added: int = 0
    shards_removed: int = 0
    waves_added: int = 0
    waves_removed: int = 0


class AdaptationRecord(BaseModel):
    """Record of an applied adaptation for manifest history."""

    model_config = ConfigDict(use_enum_values=True)

    version: int
    timestamp: str = ""
    triggers: list[str] = Field(default_factory=list)
    severity: AdaptationSeverity = AdaptationSeverity.MINOR
    changes_summary: str = ""
    shards_added: int = 0
    shards_removed: int = 0
    waves_added: int = 0
    auto_approved: bool = False
