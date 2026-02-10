"""Technical debt models — DebtEntry, DebtRegistry, RefactorClassification.

PRD-CORE-016: Proactive Refactoring Workflow.
Provides the 2x2 classification system, debt registry schema,
and budget calculation models.
"""

from __future__ import annotations

from enum import Enum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field


class RefactorImpact(str, Enum):
    """Refactor impact axis (blocking vs deferrable)."""

    BLOCKING = "blocking"
    DEFERRABLE = "deferrable"


class RefactorScope(str, Enum):
    """Refactor scope axis (local vs architectural)."""

    LOCAL = "local"
    ARCHITECTURAL = "architectural"


class RefactorClassification(str, Enum):
    """2x2 refactor classification (PRD-CORE-016-REQ-001).

    Combines impact (blocking/deferrable) and scope (local/architectural).
    """

    BLOCKING_LOCAL = "blocking-local"
    BLOCKING_ARCHITECTURAL = "blocking-architectural"
    DEFERRABLE_LOCAL = "deferrable-local"
    DEFERRABLE_ARCHITECTURAL = "deferrable-architectural"

    @staticmethod
    def classify(
        blocks_output_contract: bool,
        changes_interface: bool,
    ) -> "RefactorClassification":
        """Classify a refactor using the 2x2 decision heuristic.

        Args:
            blocks_output_contract: Can the current shard complete without this?
                False = blocking, True = deferrable.
            changes_interface: Does this change an interface other modules depend on?
                True = architectural, False = local.

        Returns:
            The appropriate classification.
        """
        impact = RefactorImpact.DEFERRABLE if blocks_output_contract else RefactorImpact.BLOCKING
        scope = RefactorScope.ARCHITECTURAL if changes_interface else RefactorScope.LOCAL
        return _CLASSIFICATION_MATRIX[(impact, scope)]


# 2x2 matrix mapping (impact, scope) to classification
_CLASSIFICATION_MATRIX: dict[
    tuple[RefactorImpact, RefactorScope], RefactorClassification
] = {
    (RefactorImpact.BLOCKING, RefactorScope.LOCAL): RefactorClassification.BLOCKING_LOCAL,
    (RefactorImpact.BLOCKING, RefactorScope.ARCHITECTURAL): RefactorClassification.BLOCKING_ARCHITECTURAL,
    (RefactorImpact.DEFERRABLE, RefactorScope.LOCAL): RefactorClassification.DEFERRABLE_LOCAL,
    (RefactorImpact.DEFERRABLE, RefactorScope.ARCHITECTURAL): RefactorClassification.DEFERRABLE_ARCHITECTURAL,
}


# Prescribed actions per classification (REQ-001)
CLASSIFICATION_ACTIONS: dict[str, dict[str, str]] = {
    "blocking-local": {
        "action": "Inline refactor within current shard. Separate commit.",
        "tracking": "QOL log entry",
    },
    "blocking-architectural": {
        "action": "Create prerequisite PRD. Phase regression to PLAN. Execute as separate wave.",
        "tracking": "PRD + events.jsonl",
    },
    "deferrable-local": {
        "action": "P2 TODO or QOL fix if <10 lines.",
        "tracking": "Debt registry entry",
    },
    "deferrable-architectural": {
        "action": "Create P2-P3 PRD. Add to roadmap backlog.",
        "tracking": "PRD + debt registry entry",
    },
}


class DebtCategory(str, Enum):
    """Technical debt category taxonomy (adapted from Google's 10 categories)."""

    MIGRATION_NEEDED = "migration_needed"
    MISSING_DOCUMENTATION = "missing_documentation"
    INADEQUATE_TESTING = "inadequate_testing"
    CODE_QUALITY = "code_quality"
    DEAD_CODE = "dead_code"
    CODE_DUPLICATION = "code_duplication"
    KNOWLEDGE_GAP = "knowledge_gap"
    PROBLEMATIC_DEPENDENCY = "problematic_dependency"
    FAILED_MIGRATION = "failed_migration"
    ARCHITECTURE_VIOLATION = "architecture_violation"


class DebtPriority(str, Enum):
    """Debt item priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DebtStatus(str, Enum):
    """Debt item lifecycle status."""

    DISCOVERED = "discovered"
    ASSESSED = "assessed"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class DebtEntry(BaseModel):
    """Single technical debt registry entry (PRD-CORE-016-REQ-005)."""

    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    id: str
    title: str
    description: str = ""
    classification: str = RefactorClassification.DEFERRABLE_LOCAL.value
    priority: str = DebtPriority.MEDIUM.value
    category: str = DebtCategory.CODE_QUALITY.value
    discovered_at: str = ""
    discovered_in: str = ""
    discovered_by: str = ""
    affected_files: list[str] = Field(default_factory=list)
    decay_score: float = Field(ge=0.0, le=1.0, default=0.5)
    last_assessed_at: str = ""
    assessment_count: int = Field(ge=0, default=1)
    estimated_effort: str = ""
    estimated_impact: str = ""
    status: str = DebtStatus.DISCOVERED.value
    resolved_by_prd: str | None = None
    resolved_at: str | None = None

    def compute_decay_score(
        self,
        days_since_discovery: float,
        *,
        base_score: float = 0.3,
        daily_rate: float = 0.01,
        assessment_rate: float = 0.05,
    ) -> float:
        """Compute updated decay score based on time and assessment count.

        Formula: min(1.0, base_score + days * daily_rate + assessments * assessment_rate)

        Args:
            days_since_discovery: Days since the debt was first discovered.
            base_score: Base score for newly discovered items (config: debt_decay_base_score).
            daily_rate: Score increment per day (config: debt_decay_daily_rate).
            assessment_rate: Score increment per assessment (config: debt_decay_assessment_rate).

        Returns:
            Updated decay score (0.0 to 1.0).
        """
        score = base_score + (days_since_discovery * daily_rate) + (self.assessment_count * assessment_rate)
        return min(1.0, round(score, 4))

    def should_auto_promote(
        self,
        *,
        threshold: float = 0.9,
    ) -> bool:
        """Check if this entry should be auto-promoted to critical.

        Args:
            threshold: Decay score threshold for auto-promotion
                (config: debt_auto_promote_threshold).

        Returns:
            True if decay_score >= threshold and priority is not already critical.
        """
        return self.decay_score >= threshold and self.priority != DebtPriority.CRITICAL.value


class DebtRegistry(BaseModel):
    """Technical debt registry (PRD-CORE-016-REQ-005).

    Persisted as .trw/debt-registry.yaml.
    """

    model_config = ConfigDict(use_enum_values=True)

    version: str = "1.0"
    entries: list[DebtEntry] = Field(default_factory=list)

    def next_id(self, *, prefix: str = "DEBT") -> str:
        """Generate the next sequential debt ID.

        Args:
            prefix: ID prefix (config: debt_id_prefix).

        Returns:
            Next debt ID string (e.g., "DEBT-001").
        """
        if not self.entries:
            return f"{prefix}-001"
        max_num = 0
        for entry in self.entries:
            try:
                num = int(entry.id.split("-")[1])
                max_num = max(max_num, num)
            except (IndexError, ValueError):
                continue
        return f"{prefix}-{max_num + 1:03d}"

    def get_actionable(
        self,
        *,
        decay_threshold: float = 0.7,
    ) -> list[DebtEntry]:
        """Get entries above the decay threshold that need attention.

        Args:
            decay_threshold: Minimum decay score to include
                (config: debt_actionable_threshold).

        Returns:
            List of entries above threshold, sorted by decay_score descending.
        """
        actionable = [
            e for e in self.entries
            if e.decay_score >= decay_threshold
            and e.status not in (DebtStatus.RESOLVED.value, DebtStatus.IN_PROGRESS.value)
        ]
        return sorted(actionable, key=lambda e: e.decay_score, reverse=True)


def compute_refactoring_budget(
    total_shards: int,
    has_critical_debt: bool,
    has_high_debt: bool,
    *,
    critical_ratio: float = 0.20,
    high_ratio: float = 0.15,
) -> dict[str, int]:
    """Compute refactoring shard allocation per wave (REQ-004).

    Rules:
    - critical debt: allocate first, up to critical_ratio of capacity
    - high debt or decay > threshold: at least high_ratio of capacity
    - no debt above threshold: 0 refactoring shards

    Args:
        total_shards: Total planned shards in the wave.
        has_critical_debt: Whether critical-priority debt items exist.
        has_high_debt: Whether high-priority or high-decay items exist.
        critical_ratio: Maximum budget fraction for critical debt
            (config: debt_budget_critical_ratio).
        high_ratio: Minimum budget fraction for high debt
            (config: debt_budget_high_ratio).

    Returns:
        Dict with refactor_shards and feature_shards counts.
    """
    if has_critical_debt:
        refactor_count = min(ceil(total_shards * critical_ratio), total_shards)
    elif has_high_debt:
        refactor_count = max(1, ceil(total_shards * high_ratio))
    else:
        refactor_count = 0

    feature_count = total_shards - refactor_count
    return {
        "refactor_shards": refactor_count,
        "feature_shards": feature_count,
    }
