"""TRW Pydantic models — public re-exports for kept model sub-modules."""

# config
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

# requirements
from trw_mcp.models.requirements import (
    ComplexityFactor,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDMetrics,
    PRDQualityGates,
    PRDTraceability,
    Requirement,
    RiskLevel,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
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

# run
from trw_mcp.models.run import (
    Event,
    EventType,
    OutputContract,
    PHASE_ORDER,
    Phase,
    ReviewFinding,
    ReversionTrigger,
    RunState,
    ShardCard,
    WaveEntry,
    WaveManifest,
)

__all__ = [
    # config
    "PhaseTimeCaps",
    "TRWConfig",
    # learning
    "ContextArchitecture",
    "ContextConventions",
    "LearningEntry",
    "LearningIndex",
    "LearningStatus",
    "Pattern",
    "PatternIndex",
    "Reflection",
    "Script",
    "ScriptIndex",
    # requirements
    "ComplexityFactor",
    "PRDConfidence",
    "PRDDates",
    "PRDEvidence",
    "PRDFrontmatter",
    "PRDMetrics",
    "PRDQualityGates",
    "PRDTraceability",
    "Requirement",
    "RiskLevel",
    "TraceabilityResult",
    "ValidationFailure",
    "ValidationResult",
    # report
    "BuildSummary",
    "DurationInfo",
    "EventSummary",
    "LearningSummary",
    "PhaseEntry",
    "RunReport",
    # run
    "Event",
    "EventType",
    "OutputContract",
    "PHASE_ORDER",
    "Phase",
    "PhaseTimeCaps",
    "ReviewFinding",
    "ReversionTrigger",
    "RunState",
    "ShardCard",
    "WaveEntry",
    "WaveManifest",
]
