"""Run state models — RunState, ShardCard, WaveManifest, Event.

These models represent the core orchestration state persisted as YAML/JSONL
in the run directory structure defined by FRAMEWORK.md v18.0_TRW.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# PRD-CORE-001: Base MCP tool suite — run state models


class Phase(str, Enum):
    """Framework execution phases (FRAMEWORK.md §PHASES)."""

    RESEARCH = "research"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VALIDATE = "validate"
    REVIEW = "review"
    DELIVER = "deliver"


class Confidence(str, Enum):
    """Confidence level for shards and runs."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @staticmethod
    def from_score(score: float) -> "Confidence":
        """Map a numeric confidence score to a categorical level.

        Bridges the AARE-F percentile scale with the framework's categorical scale:
        >=0.85 → high, >=0.70 → medium, <0.70 → low.

        Args:
            score: Confidence score from 0.0 to 1.0.

        Returns:
            Corresponding Confidence level.
        """
        if score >= 0.85:
            return Confidence.HIGH
        if score >= 0.70:
            return Confidence.MEDIUM
        return Confidence.LOW


class RunStatus(str, Enum):
    """Run lifecycle status."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"


class ShardStatus(str, Enum):
    """Individual shard execution status."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class WaveStatus(str, Enum):
    """Wave execution status."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"
    PARTIAL = "partial"


class OutputContract(BaseModel):
    """Output contract for a shard — defines expected deliverables.

    Each shard declares what files it will produce and what schema
    those files must conform to.
    """

    model_config = ConfigDict(populate_by_name=True)

    file: str
    schema_keys: list[str] = Field(default_factory=list, alias="keys")
    required: bool = True
    optional_keys: list[str] = Field(default_factory=list)


class ShardCard(BaseModel):
    """Shard card — unit of parallel work (FRAMEWORK.md §SHARD-CARDS).

    Shards are the fundamental unit of parallelism. Each shard card
    describes what the shard will do, what it needs, and what it produces.
    """

    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    id: str
    title: str
    wave: int = Field(ge=1)
    goals: list[str] = Field(default_factory=list)
    planned_outputs: list[str] = Field(default_factory=list)
    output_contract: OutputContract | None = None
    input_refs: list[str] = Field(default_factory=list)
    self_decompose: bool = True
    max_child_depth: int = 2
    confidence: Confidence = Confidence.MEDIUM
    status: ShardStatus = ShardStatus.PENDING


class WaveEntry(BaseModel):
    """Single wave in a wave manifest."""

    model_config = ConfigDict(use_enum_values=True)

    wave: int = Field(ge=1)
    shards: list[str] = Field(default_factory=list)
    status: WaveStatus = WaveStatus.PENDING
    depends_on: list[int] = Field(default_factory=list)


class WaveManifest(BaseModel):
    """Wave manifest — tracks wave-level execution progress.

    Waves are sequential groups of parallel shards. Each wave
    completes before the next begins (inter-wave data dependencies).
    """

    waves: list[WaveEntry] = Field(default_factory=list)


class RunState(BaseModel):
    """Run state — top-level orchestration state (persisted as run.yaml).

    Represents the current state of a framework execution run.
    Written atomically to disk at every state change.
    """

    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    task: str
    framework: str = "v18.0_TRW"
    status: RunStatus = RunStatus.ACTIVE
    phase: Phase = Phase.RESEARCH
    confidence: Confidence = Confidence.MEDIUM
    objective: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    prd_scope: list[str] = Field(default_factory=list)
    run_type: str = "implementation"


class Event(BaseModel):
    """Structured event for events.jsonl audit log.

    Every significant operation produces an event. Events are
    append-only and form the complete audit trail of a run.
    """

    ts: datetime
    event: str
    data: dict[str, str | int | float | bool | list[str] | None] = Field(
        default_factory=dict,
    )


# Re-export PhaseTimeCaps from config for convenience
from trw_mcp.models.config import PhaseTimeCaps as PhaseTimeCaps  # noqa: E402
