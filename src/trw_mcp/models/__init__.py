"""TRW Pydantic models — public re-exports for kept model sub-modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.task_profile import resolve_task_profile as resolve_task_profile

# config
# typed_dicts — canonical home for all TypedDicts (types.py re-exports from here)
from trw_mcp.models import typed_dicts
from trw_mcp.models.config import PhaseTimeCaps, TRWConfig

# learning
from trw_mcp.models.learning import (
    ContextArchitecture,
    ContextConventions,
    LearningEntry,
    LearningIndex,
    LearningStatus,
    Pattern,
    PatternIndex,
    Reflection,
    Script,
    ScriptIndex,
)

# report
from trw_mcp.models.report import (
    BuildSummary,
    DurationInfo,
    EventSummary,
    LearningSummary,
    PhaseEntry,
    RunReport,
)

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
    Requirement,
    RiskLevel,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
    VerificationMapping,
    VerificationMethod,
)

# run
from trw_mcp.models.run import (
    PHASE_ORDER,
    Event,
    EventType,
    OutputContract,
    Phase,
    ReversionTrigger,
    ReviewFinding,
    RunState,
    ShardCard,
    WaveEntry,
    WaveManifest,
)
from trw_mcp.models.task_profile_types import TaskProfile, TaskProfileOverrides
from trw_mcp.models.typed_dicts import (
    AutoProgressStepResult,
    CeremonyFeedbackEntry,
    CeremonyScoreResult,
    CheckpointEventDataDict,
    CheckpointRecordDict,
    CheckpointResultDict,
    DeliverResultDict,
    DeployFrameworksVersionDataDict,
    DimensionScoreDict,
    EmbedHealthStatus,
    EscalationResult,
    ImprovementSuggestionDict,
    LearningEntryCompactDict,
    LearningEntryDict,
    LearnResult,
    LearnResultDict,
    MypyResultDict,
    OutcomeCorrelationStepResult,
    ProgressionItem,
    PytestResultDict,
    RecallOutcomeStepResult,
    RecallResultDict,
    ReviewFindingDict,
    RunStatusDict,
    SectionScoreDict,
    SessionStartResultDict,
    TelemetryStepResult,
    TierDistribution,
    TierSweepStepResult,
    TrustIncrementResult,
    TrwInitConfigDataDict,
    ValidateResultDict,
    ValidationFailureDict,
)

__all__ = [
    "PHASE_ORDER",
    "AutoProgressStepResult",
    "BuildSummary",
    "CeremonyFeedbackEntry",
    "CeremonyScoreResult",
    "CheckpointEventDataDict",
    "CheckpointRecordDict",
    "CheckpointResultDict",
    "ComplexityFactor",
    "ContextArchitecture",
    "ContextConventions",
    "DeliverResultDict",
    "DeployFrameworksVersionDataDict",
    "DimensionScoreDict",
    "DurationInfo",
    "EmbedHealthStatus",
    "EscalationResult",
    "Event",
    "EventSummary",
    "EventType",
    "ImprovementSuggestionDict",
    "LearnResult",
    "LearnResultDict",
    "LearningEntry",
    "LearningEntryCompactDict",
    "LearningEntryDict",
    "LearningIndex",
    "LearningStatus",
    "LearningSummary",
    "MypyResultDict",
    "OutcomeCorrelationStepResult",
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
    "PatternIndex",
    "Phase",
    "PhaseEntry",
    "PhaseTimeCaps",
    "ProgressionItem",
    "PytestResultDict",
    "RecallOutcomeStepResult",
    "RecallResultDict",
    "Reflection",
    "Requirement",
    "ReversionTrigger",
    "ReviewFinding",
    "ReviewFindingDict",
    "RiskLevel",
    "RunReport",
    "RunState",
    "RunStatusDict",
    "Script",
    "ScriptIndex",
    "SectionScoreDict",
    "SessionStartResultDict",
    "ShardCard",
    "TRWConfig",
    "TaskProfile",
    "TaskProfileOverrides",
    "TelemetryStepResult",
    "TierDistribution",
    "TierSweepStepResult",
    "TraceabilityResult",
    "TrustIncrementResult",
    "TrwInitConfigDataDict",
    "ValidateResultDict",
    "ValidationFailure",
    "ValidationFailureDict",
    "ValidationResult",
    "VerificationMapping",
    "VerificationMethod",
    "WaveEntry",
    "WaveManifest",
    "resolve_task_profile",
    "typed_dicts",
]


def __getattr__(name: str) -> object:
    """Lazily expose the task-profile resolver without cycling through scoring."""
    if name == "resolve_task_profile":
        from trw_mcp.models.task_profile import resolve_task_profile

        return resolve_task_profile
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
