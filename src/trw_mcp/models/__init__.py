"""TRW Pydantic models — public re-exports for kept model sub-modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.task_profile import resolve_task_profile as resolve_task_profile

# config
# typed_dicts — canonical home for all TypedDicts (types.py re-exports from here)
from trw_mcp.models import typed_dicts
from trw_mcp.models.config import TRWConfig

# learning
from trw_mcp.models.learning import LearningEntry, LearningStatus, Pattern

# report
from trw_mcp.models.report import DurationInfo, EventSummary, PhaseEntry

# requirements
from trw_mcp.models.requirements import (
    ComplexityFactor,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDLifecycleStatus,
    PRDMetrics,
    PRDQualityGates,
    PRDQualityTier,
    PRDTraceability,
    PRDVerification,
    RiskLevel,
    ValidationFailure,
    ValidationResult,
    VerificationMapping,
    VerificationMethod,
)

# run
from trw_mcp.models.run import PHASE_ORDER, Event, OutputContract, Phase, ReviewFinding, RunState, ShardCard, WaveEntry
from trw_mcp.models.task_profile_types import TaskProfile, TaskProfileOverrides
from trw_mcp.models.typed_dicts import (
    AutoProgressStepResult,
    CeremonyFeedbackEntry,
    CeremonyScoreResult,
    CheckpointEventDataDict,
    CheckpointRecordDict,
    DeliverResultDict,
    DimensionScoreDict,
    EscalationResult,
    ImprovementSuggestionDict,
    LearningEntryCompactDict,
    LearningEntryDict,
    LearnResultDict,
    ProgressionItem,
    RecallResultDict,
    ReviewFindingDict,
    RunStatusDict,
    SectionScoreDict,
    SessionStartResultDict,
    TelemetryStepResult,
    TierDistribution,
    TierSweepStepResult,
    TrustIncrementResult,
    ValidateResultDict,
    ValidationFailureDict,
)

__all__ = [
    "PHASE_ORDER",
    "AutoProgressStepResult",
    "CeremonyFeedbackEntry",
    "CeremonyScoreResult",
    "CheckpointEventDataDict",
    "CheckpointRecordDict",
    "ComplexityFactor",
    "DeliverResultDict",
    "DimensionScoreDict",
    "DurationInfo",
    "EscalationResult",
    "Event",
    "EventSummary",
    "ImprovementSuggestionDict",
    "LearnResultDict",
    "LearningEntry",
    "LearningEntryCompactDict",
    "LearningEntryDict",
    "LearningStatus",
    "OutputContract",
    "PRDConfidence",
    "PRDDates",
    "PRDEvidence",
    "PRDFrontmatter",
    "PRDLifecycleStatus",
    "PRDMetrics",
    "PRDQualityGates",
    "PRDQualityTier",
    "PRDTraceability",
    "PRDVerification",
    "Pattern",
    "Phase",
    "PhaseEntry",
    "ProgressionItem",
    "RecallResultDict",
    "ReviewFinding",
    "ReviewFindingDict",
    "RiskLevel",
    "RunState",
    "RunStatusDict",
    "SectionScoreDict",
    "SessionStartResultDict",
    "ShardCard",
    "TRWConfig",
    "TaskProfile",
    "TaskProfileOverrides",
    "TelemetryStepResult",
    "TierDistribution",
    "TierSweepStepResult",
    "TrustIncrementResult",
    "ValidateResultDict",
    "ValidationFailure",
    "ValidationFailureDict",
    "ValidationResult",
    "VerificationMapping",
    "VerificationMethod",
    "WaveEntry",
    "resolve_task_profile",
    "typed_dicts",
]


def __getattr__(name: str) -> object:
    """Lazily expose the task-profile resolver without cycling through scoring."""
    if name == "resolve_task_profile":
        from trw_mcp.models.task_profile import resolve_task_profile

        return resolve_task_profile
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
