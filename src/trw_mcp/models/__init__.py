"""TRW Pydantic models — public re-exports for all model sub-modules."""

# architecture (PRD-QUAL-007)
from trw_mcp.models.architecture import (
    ArchitectureConfig,
    ArchitectureFitnessResult,
    ArchitectureStyle,
    BoundedContext,
    Convention,
    ConventionSeverity,
    ConventionViolation,
    DependencyRule,
    ImportViolation,
    TestLayerConfig,
)

# bdd (PRD-CORE-005)
from trw_mcp.models.bdd import (
    ConfidenceLevel as BDDConfidenceLevel,
    BDDGenerationResult,
    ExtractedAC,
    ExtractedFR,
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
)

# compliance (PRD-CORE-014)
from trw_mcp.models.compliance import (
    ComplianceDimension,
    ComplianceMode,
    ComplianceReport,
    ComplianceStatus,
    DimensionResult,
)

# config
from trw_mcp.models.config import PhaseTimeCaps, TRWConfig

# debt (PRD-CORE-016)
from trw_mcp.models.debt import (
    CLASSIFICATION_ACTIONS,
    DebtCategory,
    DebtEntry,
    DebtPriority,
    DebtRegistry,
    DebtStatus,
    RefactorClassification,
    RefactorImpact,
    RefactorScope,
)

# framework (PRD-CORE-017)
from trw_mcp.models.framework import (
    FrameworkVersion,
    OverlayPhase,
    OverlayRegistry,
    PhaseOverlay,
    VocabularyEntry,
    VocabularyRegistry,
)

# gate (PRD-QUAL-005)
from trw_mcp.models.gate import (
    BudgetAction,
    CostConfig,
    EscalationConfig,
    EvaluationOutcome,
    EvaluationResult,
    FallbackAction,
    FallbackConfig,
    GateConfig,
    GatePreset,
    GateStrategy,
    GateType,
    JudgeVote,
    ModelTier,
    RubricWeights,
    TieStrategy,
)

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

# planning
from trw_mcp.models.planning import (
    AgentRole,
    GroomingPlan,
    PLANNING_AGENT_ROLES,
    ResearchScope,
    SectionAnalysis,
    SectionStatus,
)

# requirements
from trw_mcp.models.requirements import (
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDMetrics,
    PRDQualityGates,
    PRDTraceability,
    Requirement,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
)

# run
from trw_mcp.models.run import (
    Event,
    OutputContract,
    PHASE_ORDER,
    ReversionTrigger,
    RunState,
    ShardCard,
    WaveEntry,
    WaveManifest,
)

# testing (PRD-QUAL-006)
from trw_mcp.models.testing import (
    PHASE_TEST_STRATEGIES,
    TestDependencyMap,
    TestMapping,
    TestResolution,
    TestStrategy,
    TestType,
)

# track (PRD-CORE-003)
from trw_mcp.models.track import (
    ConflictSeverity,
    FileConflict,
    MergeRecommendation,
    Track,
    TrackRegistry,
    TrackStatus,
)

# velocity (PRD-CORE-015)
from trw_mcp.models.velocity import (
    DebtIndicators,
    LearningSnapshot,
    OverheadMetrics,
    TrendResult,
    VelocityAlert,
    VelocityHistory,
    VelocityMetrics,
    VelocitySnapshot,
    VelocitySummary,
)

# wave (PRD-CORE-006)
from trw_mcp.models.wave import (
    AdaptationAction,
    AdaptationProposal,
    AdaptationRecord,
    AdaptationSeverity,
    AdaptationTrigger,
    AdaptationTriggerType,
    ProposedChange,
)

__all__ = [
    # architecture (PRD-QUAL-007)
    "ArchitectureConfig",
    "ArchitectureFitnessResult",
    "ArchitectureStyle",
    "BoundedContext",
    "Convention",
    "ConventionSeverity",
    "ConventionViolation",
    "DependencyRule",
    "ImportViolation",
    "TestLayerConfig",
    # bdd (PRD-CORE-005)
    "BDDConfidenceLevel",
    "BDDGenerationResult",
    "ExtractedAC",
    "ExtractedFR",
    "GherkinFeature",
    "GherkinScenario",
    "GherkinStep",
    # compliance (PRD-CORE-014)
    "ComplianceDimension",
    "ComplianceMode",
    "ComplianceReport",
    "ComplianceStatus",
    "DimensionResult",
    # config
    "TRWConfig",
    # debt (PRD-CORE-016)
    "CLASSIFICATION_ACTIONS",
    "DebtCategory",
    "DebtEntry",
    "DebtPriority",
    "DebtRegistry",
    "DebtStatus",
    "RefactorClassification",
    "RefactorImpact",
    "RefactorScope",
    # framework (PRD-CORE-017)
    "FrameworkVersion",
    "OverlayPhase",
    "OverlayRegistry",
    "PhaseOverlay",
    "VocabularyEntry",
    "VocabularyRegistry",
    # gate (PRD-QUAL-005)
    "BudgetAction",
    "CostConfig",
    "EscalationConfig",
    "EvaluationOutcome",
    "EvaluationResult",
    "FallbackAction",
    "FallbackConfig",
    "GateConfig",
    "GatePreset",
    "GateStrategy",
    "GateType",
    "JudgeVote",
    "ModelTier",
    "RubricWeights",
    "TieStrategy",
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
    # planning
    "AgentRole",
    "GroomingPlan",
    "PLANNING_AGENT_ROLES",
    "ResearchScope",
    "SectionAnalysis",
    "SectionStatus",
    # requirements
    "PRDConfidence",
    "PRDDates",
    "PRDEvidence",
    "PRDFrontmatter",
    "PRDMetrics",
    "PRDQualityGates",
    "PRDTraceability",
    "Requirement",
    "TraceabilityResult",
    "ValidationFailure",
    "ValidationResult",
    # run
    "Event",
    "OutputContract",
    "PHASE_ORDER",
    "PhaseTimeCaps",
    "ReversionTrigger",
    "RunState",
    "ShardCard",
    "WaveEntry",
    "WaveManifest",
    # testing (PRD-QUAL-006)
    "PHASE_TEST_STRATEGIES",
    "TestDependencyMap",
    "TestMapping",
    "TestResolution",
    "TestStrategy",
    "TestType",
    # track (PRD-CORE-003)
    "ConflictSeverity",
    "FileConflict",
    "MergeRecommendation",
    "Track",
    "TrackRegistry",
    "TrackStatus",
    # velocity (PRD-CORE-015)
    "DebtIndicators",
    "LearningSnapshot",
    "OverheadMetrics",
    "TrendResult",
    "VelocityAlert",
    "VelocityHistory",
    "VelocityMetrics",
    "VelocitySnapshot",
    "VelocitySummary",
    # wave (PRD-CORE-006)
    "AdaptationAction",
    "AdaptationProposal",
    "AdaptationRecord",
    "AdaptationSeverity",
    "AdaptationTrigger",
    "AdaptationTriggerType",
    "ProposedChange",
]
