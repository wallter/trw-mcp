"""Typed dict shapes for cross-module data flowing through trw-mcp.

These replace ``dict[str, object]`` at critical boundaries (tool returns,
adapter outputs, validation results) so downstream consumers get static
type checking instead of guessing at dict shapes via ``.get()`` and casts.

Usage::

    from trw_mcp.models.typed_dicts import LearningEntryDict, RecallResultDict

Convention: *Dict suffix for TypedDict classes.  ``total=False`` on dicts
that have optional keys added conditionally.

Submodule layout
----------------
_learning      — LearningEntryCompactDict, LearningEntryDict
_validation    — DimensionScoreDict, ValidateResultDict, etc.
_tools         — RecallResultDict, SessionStartResultDict, DeliverResultDict, etc.
_build         — PytestResultDict, MypyResultDict, PipAuditResult, etc.
_review        — ReviewFindingDict, ManualReviewResult, CrossModelReviewResult, etc.
_ceremony      — CeremonyScoreResult, CeremonyFeedbackEntry, EscalationResult, etc.
_analytics     — RunAnalysisResult, AggregateMetrics, AnalyticsReport, etc.
_delivery      — TrustIncrementResult, TelemetryStepResult, PublishLearningsResult, etc.
_audit         — AuditReport, AuditLearningsResult, etc.
_dashboard     — CeremonyTrendResult, CoverageTrendResult, ReviewTrendResult, etc.
_orchestration — TrwStatusDict, WaveProgressDict, CheckpointRecordDict, etc.
_trust         — TrustLevelResult, HumanReviewResult, TrustSessionIncrementResult
_export        — ExportSummary, ExportMetadata, SyncIndexMdResult, etc.
_dedup         — DedupHandleResult
_bootstrap     — BootstrapFileResult (IDE config generation return shapes)
_opencode      — OpencodeServerEntry, OpencodeConfig, OpencodeTemplateDict
""

from __future__ import annotations

# _learning
from trw_mcp.models.typed_dicts._learning import (
    LearningEntryCompactDict,
    LearningEntryDict,
    PruneCandidateDict,
)

# _validation
from trw_mcp.models.typed_dicts._validation import (
    DimensionScoreDict,
    ImprovementSuggestionDict,
    PrdCreateResultDict,
    SectionScoreDict,
    ValidateResultDict,
    ValidationFailureDict,
)

# _tools
from trw_mcp.models.typed_dicts._tools import (
    CheckpointResultDict,
    DeliverResultDict,
    KnowledgeSyncResultDict,
    LearnResultDict,
    PreCompactResultDict,
    ProgressiveExpandResult,
    RecallContextDict,
    RecallResultDict,
    RunReportResultDict,
    RunStatusDict,
    SessionStartResultDict,
    TelemetryRecordDict,
    ToolEventDataDict,
    UsageCallerEntryDict,
    UsageGroupEntryDict,
    UsageModelEntryDict,
    UsageReportResult,
)

# _build
from trw_mcp.models.typed_dicts._build import (
    ApiFuzzResult,
    DepAuditResult,
    MypyResultDict,
    NpmAuditResult,
    PipAuditResult,
    PytestResultDict,
)

# _review
from trw_mcp.models.typed_dicts._review import (
    AutoReviewResult,
    CrossModelReviewResult,
    ManualReviewResult,
    MultiReviewerAnalysisResult,
    ReconcileReviewResult,
    ReviewFindingDict,
    ReviewModeResult,
    ReviewResultBase,
)

# _ceremony
from trw_mcp.models.typed_dicts._ceremony import (
    AutoMaintenanceDict,
    AutoRecalledItemDict,
    CeremonyApproveResult,
    CeremonyClassStatusDict,
    CeremonyFeedbackEntry,
    CeremonyRevertResult,
    CeremonyScoreResult,
    CeremonyStatusResult,
    ClaudeMdSyncResultDict,
    ComplianceArtifactsDict,
    DeliveryGatesDict,
    EscalationResult,
    FinalizeRunResult,
    ReductionProposalDict,
    ReflectResultDict,
    SessionRecallExtrasDict,
    TierCeremonyScoreResult,
)

# _analytics
from trw_mcp.models.typed_dicts._analytics import (
    AggregateMetrics,
    AnalyticsReport,
    BatchDedupResult,
    CeremonyTrendItem,
    EmbedHealthStatus,
    ImpactDistributionResult,
    ImpactTierInfo,
    RecallStats,
    RunAnalysisResult,
    TierDistribution,
    TierMetrics,
)

# _delivery
from trw_mcp.models.typed_dicts._delivery import (
    AutoProgressStepResult,
    BatchSendResult,
    CeremonyFeedbackStepResult,
    ConsolidationStepResult,
    IndexSyncResult,
    LearnResult,
    OutcomeCorrelationStepResult,
    ProgressionItem,
    PublishLearningsResult,
    PublishResult,
    RecallOutcomeStepResult,
    StepResultBase,
    TelemetryStepResult,
    TierSweepStepResult,
    TrustIncrementResult,
)

# _audit
from trw_mcp.models.typed_dicts._audit import (
    AuditCeremonyComplianceResult,
    AuditDuplicatePairDict,
    AuditDuplicatesResult,
    AuditFixActionsDict,
    AuditHookVersionsResult,
    AuditIndexConsistencyResult,
    AuditLearningsResult,
    AuditRecallEffectivenessResult,
    AuditReflectionComponentsDict,
    AuditReflectionDiagnosticsDict,
    AuditReflectionQualityResult,
    AuditReport,
    AuditTelemetryBloatDict,
)

# _dashboard
from trw_mcp.models.typed_dicts._dashboard import (
    CeremonyTrendResult,
    CoverageTrendResult,
    DegradationAlertResult,
    ReviewTrendResult,
)

# _orchestration
from trw_mcp.models.typed_dicts._orchestration import (
    CheckpointEventDataDict,
    CheckpointRecordDict,
    DeployFrameworksVersionDataDict,
    StatusReflectionDict,
    StatusReversionLatestDict,
    StatusReversionMetricsDict,
    TrwInitConfigDataDict,
    TrwStatusDict,
    WaveDetailDict,
    WaveProgressDict,
    WaveShardCountsDict,
)

# _trust
from trw_mcp.models.typed_dicts._trust import (
    HumanReviewResult,
    TrustLevelQueryResult,
    TrustLevelResult,
    TrustSessionIncrementResult,
)

# _export
from trw_mcp.models.typed_dicts._export import (
    ExportAnalyticsSection,
    ExportMetadata,
    ExportPatternsSection,
    ExportRunsSection,
    ExportSummary,
    ImportLearningsResult,
    RoadmapSyncResult,
    SyncIndexMdResult,
)

# _dedup
from trw_mcp.models.typed_dicts._dedup import (
    DedupHandleResult,
)

# _mutations
from trw_mcp.models.typed_dicts._mutations import (
    MutationCheckResult,
    MutationSkippedResult,
    ParseMutmutResultDict,
    SurvivingMutantDict,
)

# _bootstrap
from trw_mcp.models.typed_dicts._bootstrap import (
    BootstrapFileResult,
)

# _opencode
from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
)

__all__ = [
    # _learning
    "LearningEntryCompactDict",
    "LearningEntryDict",
    "PruneCandidateDict",
    # _validation
    "DimensionScoreDict",
    "ImprovementSuggestionDict",
    "PrdCreateResultDict",
    "SectionScoreDict",
    "ValidateResultDict",
    "ValidationFailureDict",
    # _tools
    "CheckpointResultDict",
    "DeliverResultDict",
    "KnowledgeSyncResultDict",
    "LearnResultDict",
    "PreCompactResultDict",
    "ProgressiveExpandResult",
    "RecallContextDict",
    "RecallResultDict",
    "RunReportResultDict",
    "RunStatusDict",
    "SessionStartResultDict",
    "TelemetryRecordDict",
    "ToolEventDataDict",
    "UsageCallerEntryDict",
    "UsageGroupEntryDict",
    "UsageModelEntryDict",
    "UsageReportResult",
    # _build
    "ApiFuzzResult",
    "DepAuditResult",
    "MypyResultDict",
    "NpmAuditResult",
    "PipAuditResult",
    "PytestResultDict",
    # _review
    "AutoReviewResult",
    "CrossModelReviewResult",
    "ManualReviewResult",
    "MultiReviewerAnalysisResult",
    "ReconcileReviewResult",
    "ReviewFindingDict",
    "ReviewModeResult",
    "ReviewResultBase",
    # _ceremony
    "AutoMaintenanceDict",
    "AutoRecalledItemDict",
    "CeremonyApproveResult",
    "CeremonyClassStatusDict",
    "CeremonyFeedbackEntry",
    "CeremonyRevertResult",
    "CeremonyScoreResult",
    "CeremonyStatusResult",
    "ClaudeMdSyncResultDict",
    "ComplianceArtifactsDict",
    "DeliveryGatesDict",
    "EscalationResult",
    "FinalizeRunResult",
    "ReductionProposalDict",
    "ReflectResultDict",
    "SessionRecallExtrasDict",
    "TierCeremonyScoreResult",
    # _analytics
    "AggregateMetrics",
    "AnalyticsReport",
    "BatchDedupResult",
    "CeremonyTrendItem",
    "EmbedHealthStatus",
    "ImpactDistributionResult",
    "ImpactTierInfo",
    "RecallStats",
    "RunAnalysisResult",
    "TierDistribution",
    "TierMetrics",
    # _delivery
    "AutoProgressStepResult",
    "BatchSendResult",
    "CeremonyFeedbackStepResult",
    "ConsolidationStepResult",
    "IndexSyncResult",
    "LearnResult",
    "OutcomeCorrelationStepResult",
    "ProgressionItem",
    "PublishLearningsResult",
    "PublishResult",
    "RecallOutcomeStepResult",
    "StepResultBase",
    "TelemetryStepResult",
    "TierSweepStepResult",
    "TrustIncrementResult",
    # _audit
    "AuditCeremonyComplianceResult",
    "AuditDuplicatePairDict",
    "AuditDuplicatesResult",
    "AuditFixActionsDict",
    "AuditHookVersionsResult",
    "AuditIndexConsistencyResult",
    "AuditLearningsResult",
    "AuditRecallEffectivenessResult",
    "AuditReflectionComponentsDict",
    "AuditReflectionDiagnosticsDict",
    "AuditReflectionQualityResult",
    "AuditReport",
    "AuditTelemetryBloatDict",
    # _dashboard
    "CeremonyTrendResult",
    "CoverageTrendResult",
    "DegradationAlertResult",
    "ReviewTrendResult",
    # _orchestration
    "CheckpointEventDataDict",
    "CheckpointRecordDict",
    "DeployFrameworksVersionDataDict",
    "StatusReflectionDict",
    "StatusReversionLatestDict",
    "StatusReversionMetricsDict",
    "TrwInitConfigDataDict",
    "TrwStatusDict",
    "WaveDetailDict",
    "WaveProgressDict",
    "WaveShardCountsDict",
    # _trust
    "HumanReviewResult",
    "TrustLevelQueryResult",
    "TrustLevelResult",
    "TrustSessionIncrementResult",
    # _export
    "ExportAnalyticsSection",
    "ExportMetadata",
    "ExportPatternsSection",
    "ExportRunsSection",
    "ExportSummary",
    "ImportLearningsResult",
    "RoadmapSyncResult",
    "SyncIndexMdResult",
    # _dedup
    "DedupHandleResult",
    # _mutations
    "MutationCheckResult",
    "MutationSkippedResult",
    "ParseMutmutResultDict",
    "SurvivingMutantDict",
    # _bootstrap
    "BootstrapFileResult",
    # _opencode
    "OpencodeConfig",
    "OpencodeServerEntry",
    "OpencodeTemplateDict",

]