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
_orchestration — TrwStatusDict, CheckpointRecordDict, etc.
_trust         — TrustLevelResult, HumanReviewResult
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
    CeremonyTrendItem,
    RunAnalysisResult,
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

# _ceremony
from trw_mcp.models.typed_dicts._ceremony import (
    AutoMaintenanceDict,
    CeremonyFeedbackEntry,
    CeremonyScoreResult,
    ClaudeMdSyncResultDict,
    ComplianceArtifactsDict,
    DeliveryGatesDict,
    EscalationResult,
    MovedCheckoutCandidateDict,
    MovedCheckoutDict,
    OpenHandoffDict,
    OpenHandoffItemDict,
    ReconciledLocalWritesDict,
    ReflectResultDict,
    SessionRecallExtrasDict,
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
from trw_mcp.models.typed_dicts._dashboard import ReviewTrendResult

# _dedup
from trw_mcp.models.typed_dicts._dedup import (
    DedupHandleResult,
)

# _delivery
from trw_mcp.models.typed_dicts._delivery import (
    AutoProgressStepResult,
    BatchSendResult,
    CeremonyFeedbackStepResult,
    IndexSyncResult,
    MemoryDecayStepResult,
    ProgressionItem,
    PublishLearningsResult,
    PublishResult,
    ReworkMetricsResult,
    StepResultBase,
    TelemetryStepResult,
    TrustIncrementResult,
)

# _export
from trw_mcp.models.typed_dicts._export import (
    ExportAnalyticsSection,
    ExportMetadata,
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
    DeliverGateScanDict,
    StatusReflectionDict,
    StatusReversionLatestDict,
    StatusReversionMetricsDict,
    TrwStatusDict,
)

# _review
from trw_mcp.models.typed_dicts._review import (
    AutoReviewResult,
    CrossModelReviewResult,
    ManualReviewResult,
    MultiReviewerAnalysisResult,
    ReconcileReviewResult,
    ReviewFindingDict,
    ReviewResultBase,
)

# _tools
from trw_mcp.models.typed_dicts._tools import (
    BuildCheckResultDict,
    CapabilityIntegrationRow,
    Degradation,
    DeliverResultDict,
    LearnResultDict,
    PreCompactResultDict,
    RecallResultDict,
    RequirementDriftEntry,
    RequirementDriftFinding,
    RequirementDriftReport,
    RunStatusDict,
    SessionStartResultDict,
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

__all__ = [
    "AggregateMetrics",
    "AnalyticsReport",
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
    "AutoReviewResult",
    "BatchSendResult",
    "BootstrapFileResult",
    "BuildCheckResultDict",
    "CapabilityIntegrationRow",
    "CodexConfigDict",
    "CodexFeaturesConfig",
    "CodexHookCommand",
    "CodexHookMatcherEntry",
    "CodexHooksConfig",
    "CodexMcpServerEntry",
    "CodexSkillConfigEntry",
    "CodexSkillsConfig",
    "CeremonyFeedbackEntry",
    "CeremonyFeedbackStepResult",
    "CeremonyScoreResult",
    "CeremonyTrendItem",
    "CheckpointEventDataDict",
    "CheckpointRecordDict",
    "ClaudeMdSyncResultDict",
    "ComplianceArtifactsDict",
    "CrossModelReviewResult",
    "DedupHandleResult",
    "Degradation",
    "DeliverGateScanDict",
    "DeliverResultDict",
    "DeliveryGatesDict",
    "MovedCheckoutCandidateDict",
    "MovedCheckoutDict",
    "OpenHandoffDict",
    "OpenHandoffItemDict",
    "ReconciledLocalWritesDict",
    "DimensionScoreDict",
    "EscalationResult",
    "ExportAnalyticsSection",
    "ExportMetadata",
    "ExportRunsSection",
    "ExportSummary",
    "ImportLearningsResult",
    "ImprovementSuggestionDict",
    "IndexSyncResult",
    "LearnResultDict",
    "LearningEntryCompactDict",
    "LearningEntryDict",
    "ManualReviewResult",
    "MultiReviewerAnalysisResult",
    "OpencodeConfig",
    "OpencodeServerEntry",
    "OpencodeTemplateDict",
    "PrdCreateResultDict",
    "PrdFrontmatterDict",
    "PreCompactResultDict",
    "ProgressionItem",
    "PruneCandidateDict",
    "PublishLearningsResult",
    "PublishResult",
    "RecallResultDict",
    "RequirementDriftEntry",
    "RequirementDriftFinding",
    "RequirementDriftReport",
    "ReworkMetricsResult",
    "ReconcileReviewResult",
    "ReflectResultDict",
    "ReviewFindingDict",
    "ReviewResultBase",
    "ReviewTrendResult",
    "RoadmapSyncResult",
    "RunAnalysisResult",
    "RunStatusDict",
    "SectionScoreDict",
    "SessionRecallExtrasDict",
    "SessionStartResultDict",
    "StatusReflectionDict",
    "StatusReversionLatestDict",
    "StatusReversionMetricsDict",
    "StepResultBase",
    "SyncIndexMdResult",
    "TelemetryStepResult",
    "TierMetrics",
    "MemoryDecayStepResult",
    "TrustIncrementResult",
    "TrwAdoptRunResultDict",
    "TrwHeartbeatResultDict",
    "TrwStatusDict",
    "ValidateResultDict",
    "ValidationFailureDict",
]
