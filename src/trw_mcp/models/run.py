"""Run state models — RunState, ShardCard, WaveManifest, Event.

These models represent the core orchestration state persisted as YAML/JSONL
in the run directory structure defined by FRAMEWORK.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# PRD-CORE-001: Base MCP tool suite — run state models


class Phase(str, Enum):
    """Framework execution phases (FRAMEWORK.md §PHASES).

    6-phase model: RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER.
    """

    RESEARCH = "research"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VALIDATE = "validate"
    REVIEW = "review"
    DELIVER = "deliver"


# Phase ordering for reversion validation (PRD-CORE-013-FR01).
# Derived from Phase enum to stay in sync automatically.
PHASE_ORDER: dict[str, int] = {phase.value: i for i, phase in enumerate(Phase)}


class ReversionTrigger(str, Enum):
    """Reversion trigger classification (PRD-CORE-013-FR02).

    Categorizes why a phase reversion was initiated.
    """

    REFACTOR_NEEDED = "refactor_needed"
    ARCHITECTURE_MISMATCH = "architecture_mismatch"
    NEW_DEPENDENCY = "new_dependency"
    TEST_STRATEGY_CHANGE = "test_strategy_change"
    SCOPE_CHANGE = "scope_change"
    OTHER = "other"

    @staticmethod
    def classify(trigger_str: str) -> "ReversionTrigger":
        """Return the matching ReversionTrigger, or OTHER if unrecognized."""
        try:
            return ReversionTrigger(trigger_str)
        except ValueError:
            return ReversionTrigger.OTHER


class Confidence(str, Enum):
    """Confidence level for shards and runs."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @staticmethod
    def from_score(score: float) -> "Confidence":
        """Map a 0.0–1.0 score to a level: >=0.85 → HIGH, >=0.70 → MEDIUM, else LOW."""
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
    PARTIAL = "partial"
    FAILED = "failed"


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

    # Integration checklist fields (Sprint 13 Track B)
    registered_in_server: bool | None = None
    documented_in_framework: bool | None = None
    configured_in_pyproject: bool | None = None
    updated_in_claude_md: bool | None = None


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
    PRD-CORE-006: version and adaptation_history for dynamic wave adaptation.
    """

    waves: list[WaveEntry] = Field(default_factory=list)
    version: int = Field(ge=1, default=1)
    adaptation_history: list[dict[str, object]] = Field(default_factory=list)


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


class EventType(str, Enum):
    """Canonical event type identifiers for the TRW event system.

    All event types used in REWARD_MAP, EVENT_ALIASES, and tool
    instrumentation must be members of this enum. This provides
    type safety and eliminates silent failures from typos.
    """

    # --- Run lifecycle ---
    RUN_INIT = "run_init"
    RUN_RESUMED = "run_resumed"
    SESSION_START = "session_start"

    # --- Phase lifecycle ---
    PHASE_ENTER = "phase_enter"
    PHASE_CHECK = "phase_check"
    PHASE_REVERT = "phase_revert"
    PHASE_GATE_PASSED = "phase_gate_passed"
    PHASE_GATE_FAILED = "phase_gate_failed"

    # --- Wave/shard lifecycle ---
    SHARD_STARTED = "shard_started"
    SHARD_COMPLETE = "shard_complete"  # backward-compat alias
    SHARD_COMPLETED = "shard_completed"
    WAVE_COMPLETE = "wave_complete"  # backward-compat alias
    WAVE_COMPLETED = "wave_completed"
    WAVE_VALIDATED = "wave_validated"
    WAVE_VALIDATION_PASSED = "wave_validation_passed"

    # --- PRD lifecycle ---
    PRD_CREATED = "prd_created"
    PRD_APPROVED = "prd_approved"
    PRD_STATUS_CHANGE = "prd_status_change"
    PRD_GROOM_COMPLETE = "prd_groom_complete"
    AUTO_PRD_PROGRESS = "auto_prd_progress"

    # --- Testing/build ---
    TESTS_PASSED = "tests_passed"
    TESTS_FAILED = "tests_failed"
    TEST_RUN = "test_run"
    BUILD_PASSED = "build_passed"
    BUILD_FAILED = "build_failed"

    # --- Learning/reflection ---
    REFLECTION_COMPLETE = "reflection_complete"  # backward-compat alias
    REFLECTION_COMPLETED = "reflection_completed"
    CHECKPOINT = "checkpoint"
    CLAUDE_MD_SYNCED = "claude_md_synced"

    # --- Telemetry (PRD-CORE-031) ---
    TOOL_INVOCATION = "tool_invocation"
    BUILD_CHECK_COMPLETE = "build_check_complete"

    # --- Compliance ---
    COMPLIANCE_CHECK = "compliance_check"
    COMPLIANCE_PASSED = "compliance_passed"

    # --- Code simplifier (PRD-QUAL-010) ---
    SIMPLIFICATION_COMPLETE = "simplification_complete"
    SIMPLIFICATION_ROLLBACK = "simplification_rollback"

    # --- File operations ---
    FILE_MODIFIED = "file_modified"

    # --- Task lifecycle ---
    TASK_COMPLETE = "task_complete"

    @staticmethod
    def resolve(event_str: str) -> "EventType | None":
        """Return the matching EventType member, or None if unrecognized."""
        try:
            return EventType(event_str)
        except ValueError:
            return None


class Event(BaseModel):
    """Structured event for events.jsonl audit log.

    Every significant operation produces an event. Events are
    append-only and form the complete audit trail of a run.
    """

    ts: datetime
    event: str
    data: dict[str, str | int | float | bool | list[str] | None] = Field(default_factory=dict)
