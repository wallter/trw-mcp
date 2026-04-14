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
_trust         — TrustLevelResult, HumanReviewResult, TrustSessionIncrementResult, TrustLevelQueryResult
_export        — ExportSummary, ExportMetadata, SyncIndexMdResult, etc.
_dedup         — DedupHandleResult
_bootstrap     — BootstrapFileResult (IDE config generation return shapes)
_codex         — CodexConfigDict, CodexHooksConfig, CodexMcpServerEntry
_opencode      — OpencodeServerEntry, OpencodeConfig, OpencodeTemplateDict
_telemetry     — RemoteSharedLearningDict
"""

from __future__ import annotations

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

# _bootstrap
from trw_mcp.models.typed_dicts._bootstrap import (
    BootstrapFileResult,
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
    TrwAdoptRunResultDict,
    TrwHeartbeatResultDict,
)

# _codex
from trw_mcp.models.typed_dicts._codex import (
    CodexConfigDict,
    CodexFeaturesConfig,
    CodexHookCommand,
    CodexHookMatcherEntry,
    CodexHooksConfig,
    CodexMcpServerEntry,
    CodexMcpToolConfigEntry,
    CodexSkillConfigEntry,
    CodexSkillsConfig,
    CodexToolApprovalMode,
)

# _dashboard
from trw_mcp.models.typed_dicts._dashboard import (
    CeremonyTrendResult,
    CoverageTrendResult,
    DegradationAlertResult,
    ReviewTrendResult,
)

# _dedup
from trw_mcp.models.typed_dicts._dedup import (
    DedupHandleResult,
)

# _delivery
from trw_mcp.models.typed_dicts._delivery import (
    AutoProgressStepResult,
    BatchSendResult,
    CeremonyFeedbackStepResult,
    ConsolidationStepResult,
    IndexSyncResult,
    OutcomeCorrelationStepResult,
    ProgressionItem,
    PublishLearningsResult,
    PublishResult,
    RecallOutcomeStepResult,
    ReworkMetricsResult,
    StepResultBase,
    TelemetryStepResult,
    TierSweepStepResult,
    TrustIncrementResult,
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

# _learning
from trw_mcp.models.typed_dicts._learning import (
    LearningEntryCompactDict,
    LearningEntryDict,
    PruneCandidateDict,
)

# _mutations
from trw_mcp.models.typed_dicts._mutations import (
    MutationCheckResult,
    MutationSkippedResult,
    ParseMutmutResultDict,
    SurvivingMutantDict,
)

# _opencode
from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
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

# _telemetry
from trw_mcp.models.typed_dicts._telemetry import (
    RemoteSharedLearningDict,
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

# _trust
from trw_mcp.models.typed_dicts._trust import (
    HumanReviewResult,
    TrustLevelQueryResult,
    TrustLevelResult,
    TrustSessionIncrementResult,
)

# _validation
from trw_mcp.models.typed_dicts._validation import (
    DimensionScoreDict,
    ImprovementSuggestionDict,
    PrdCreateResultDict,
    PrdFrontmatterDict,
    SectionScoreDict,
    ValidateResultDict,
    ValidationFailureDict,
)

# Backward-compat alias: LearnResult was merged into LearnResultDict (PRD-CORE-080).
# Consumers that import `LearnResult` continue to work unchanged.
LearnResult = LearnResultDict

__all__ = [
    "AggregateMetrics",
    "AnalyticsReport",
    "ApiFuzzResult",
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
    "AutoMaintenanceDict",
    "AutoProgressStepResult",
    "AutoRecalledItemDict",
    "AutoReviewResult",
    "BatchDedupResult",
    "BatchSendResult",
    "BootstrapFileResult",
    "CodexConfigDict",
    "CodexFeaturesConfig",
    "CodexHookCommand",
    "CodexHookMatcherEntry",
    "CodexHooksConfig",
    "CodexMcpServerEntry",
    "CodexSkillConfigEntry",
    "CodexSkillsConfig",
    "CeremonyApproveResult",
    "CeremonyClassStatusDict",
    "CeremonyFeedbackEntry",
    "CeremonyFeedbackStepResult",
    "CeremonyRevertResult",
    "CeremonyScoreResult",
    "CeremonyStatusResult",
    "CeremonyTrendItem",
    "CeremonyTrendResult",
    "CheckpointEventDataDict",
    "CheckpointRecordDict",
    "CheckpointResultDict",
    "ClaudeMdSyncResultDict",
    "ComplianceArtifactsDict",
    "ConsolidationStepResult",
    "CoverageTrendResult",
    "CrossModelReviewResult",
    "DedupHandleResult",
    "DegradationAlertResult",
    "DeliverResultDict",
    "DeliveryGatesDict",
    "DepAuditResult",
    "DeployFrameworksVersionDataDict",
    "DimensionScoreDict",
    "EmbedHealthStatus",
    "EscalationResult",
    "ExportAnalyticsSection",
    "ExportMetadata",
    "ExportPatternsSection",
    "ExportRunsSection",
    "ExportSummary",
    "FinalizeRunResult",
    "HumanReviewResult",
    "ImpactDistributionResult",
    "ImpactTierInfo",
    "ImportLearningsResult",
    "ImprovementSuggestionDict",
    "IndexSyncResult",
    "KnowledgeSyncResultDict",
    "LearnResult",  # backward-compat alias for LearnResultDict
    "LearnResultDict",
    "LearningEntryCompactDict",
    "LearningEntryDict",
    "ManualReviewResult",
    "MultiReviewerAnalysisResult",
    "MutationCheckResult",
    "MutationSkippedResult",
    "MypyResultDict",
    "NpmAuditResult",
    "OpencodeConfig",
    "OpencodeServerEntry",
    "OpencodeTemplateDict",
    "OutcomeCorrelationStepResult",
    "ParseMutmutResultDict",
    "PipAuditResult",
    "PrdCreateResultDict",
    "PrdFrontmatterDict",
    "PreCompactResultDict",
    "ProgressionItem",
    "ProgressiveExpandResult",
    "PruneCandidateDict",
    "PublishLearningsResult",
    "PublishResult",
    "PytestResultDict",
    "RecallContextDict",
    "RecallOutcomeStepResult",
    "RecallResultDict",
    "RecallStats",
    "ReworkMetricsResult",
    "ReconcileReviewResult",
    "ReductionProposalDict",
    "ReflectResultDict",
    "RemoteSharedLearningDict",
    "ReviewFindingDict",
    "ReviewModeResult",
    "ReviewResultBase",
    "ReviewTrendResult",
    "RoadmapSyncResult",
    "RunAnalysisResult",
    "RunReportResultDict",
    "RunStatusDict",
    "SectionScoreDict",
    "SessionRecallExtrasDict",
    "SessionStartResultDict",
    "StatusReflectionDict",
    "StatusReversionLatestDict",
    "StatusReversionMetricsDict",
    "StepResultBase",
    "SurvivingMutantDict",
    "SyncIndexMdResult",
    "TelemetryRecordDict",
    "TelemetryStepResult",
    "TierCeremonyScoreResult",
    "TierDistribution",
    "TierMetrics",
    "TierSweepStepResult",
    "ToolEventDataDict",
    "TrustIncrementResult",
    "TrustLevelQueryResult",
    "TrwAdoptRunResultDict",
    "TrwHeartbeatResultDict",
    "TrustLevelResult",
    "TrustSessionIncrementResult",
    "TrwInitConfigDataDict",
    "TrwStatusDict",
    "UsageCallerEntryDict",
    "UsageGroupEntryDict",
    "UsageModelEntryDict",
    "UsageReportResult",
    "ValidateResultDict",
    "ValidationFailureDict",
    "WaveDetailDict",
    "WaveProgressDict",
    "WaveShardCountsDict",
]
