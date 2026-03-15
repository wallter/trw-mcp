"""Domain sub-config models and PhaseTimeCaps.

These are composed into TRWConfig via @property accessors.
Each groups related fields for type-narrowed function signatures.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config._defaults import (
    DEFAULT_BUILD_CHECK_TIMEOUT_SECS,
    DEFAULT_LEARNING_MAX_ENTRIES,
    DEFAULT_MUTATION_TIMEOUT_SECS,
    DEFAULT_PARALLELISM_MAX,
    DEFAULT_RECALL_MAX_RESULTS,
    DEFAULT_RECALL_RECEIPT_MAX_ENTRIES,
    DEFAULT_SCORING_DEFAULT_DAYS_UNUSED,
)


class BuildConfig(BaseModel):
    """Build verification and test execution configuration."""

    model_config = ConfigDict(frozen=True)

    build_check_enabled: bool = True
    build_check_timeout_secs: int = DEFAULT_BUILD_CHECK_TIMEOUT_SECS
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: str = "lenient"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None
    run_auto_close_enabled: bool = True
    run_auto_close_age_days: int = 7
    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True
    mutation_enabled: bool = False
    mutation_threshold: float = 0.50
    mutation_threshold_critical: float = 0.70
    mutation_threshold_experimental: float = 0.30
    mutation_timeout_secs: int = DEFAULT_MUTATION_TIMEOUT_SECS


class MemoryConfig(BaseModel):
    """Learning storage, retrieval, and lifecycle configuration."""

    model_config = ConfigDict(frozen=True)

    learning_max_entries: int = DEFAULT_LEARNING_MAX_ENTRIES
    recall_receipt_max_entries: int = DEFAULT_RECALL_RECEIPT_MAX_ENTRIES
    recall_max_results: int = DEFAULT_RECALL_MAX_RESULTS
    memory_store_path: str = ".trw/memory/vectors.db"
    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85
    memory_consolidation_enabled: bool = True
    memory_consolidation_max_per_cycle: int = 50
    memory_hot_max_entries: int = 50
    memory_score_w1: float = 0.4
    memory_score_w2: float = 0.3
    memory_score_w3: float = 0.3


class TelemetryConfig(BaseModel):
    """Telemetry, OTEL, and ceremony alerting configuration."""

    model_config = ConfigDict(frozen=True)

    debug: bool = False
    platform_telemetry_enabled: bool = False
    otel_enabled: bool = False
    otel_endpoint: str = ""
    ceremony_alert_threshold: int = 40
    ceremony_alert_consecutive: int = 3


class OrchestrationConfig(BaseModel):
    """Wave/shard orchestration and agent settings."""

    model_config = ConfigDict(frozen=True)

    parallelism_max: int = DEFAULT_PARALLELISM_MAX
    timebox_hours: int = 8
    max_research_waves: int = 3
    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 5
    agent_teams_enabled: bool = True


class ScoringConfig(BaseModel):
    """Scoring weights, tier boundaries, and decay parameters."""

    model_config = ConfigDict(frozen=True)

    scoring_default_days_unused: int = DEFAULT_SCORING_DEFAULT_DAYS_UNUSED
    learning_decay_half_life_days: float = 14.0
    impact_forced_distribution_enabled: bool = True
    complexity_tier_minimal: int = 3
    complexity_tier_comprehensive: int = 7


class TrustConfig(BaseModel):
    """Progressive trust model boundaries (PRD-CORE-068)."""

    model_config = ConfigDict(frozen=True)

    trust_crawl_boundary: int = 50
    trust_walk_boundary: int = 200
    trust_walk_sample_rate: float = 0.3
    trust_security_tags: tuple[str, ...] = (
        "auth", "secrets", "permissions", "encryption", "oauth", "jwt",
    )
    trust_locked: bool = False


class CeremonyFeedbackConfig(BaseModel):
    """Self-improving ceremony feedback thresholds (PRD-CORE-069)."""

    model_config = ConfigDict(frozen=True)

    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5


class PathsConfig(BaseModel):
    """Directory structure and path defaults."""

    model_config = ConfigDict(frozen=True)

    task_root: str = "docs"
    runs_root: str = ".trw/runs"
    trw_dir: str = ".trw"
    context_dir: str = "context"
    logs_dir: str = "logs"
    source_package_path: str = "src"


class PhaseTimeCaps(BaseModel):
    """Phase time cap percentages -- ORC-level time tracking only.

    Convenience accessor for mapping phase names to their target time fractions.
    NOTE: Framework-documented defaults; not enforced by MCP tools.
    ORC tracks wall-clock time against these caps at the prompt level.
    6-phase model: RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER.
    """

    model_config = ConfigDict(frozen=True)

    research: float = 0.25
    plan: float = 0.15
    implement: float = 0.35
    validate_phase: float = 0.10
    review: float = 0.10
    deliver: float = 0.05

    # Maps canonical phase name -> field name; only "validate" differs to avoid
    # the Pydantic BaseModel reserved-name conflict.
    _PHASE_FIELDS: ClassVar[dict[str, str]] = {
        "research": "research",
        "plan": "plan",
        "implement": "implement",
        "validate": "validate_phase",  # field renamed to avoid BaseModel collision
        "review": "review",
        "deliver": "deliver",
    }

    def get_cap(self, phase: str) -> float:
        """Return the time cap fraction for the given phase.

        Args:
            phase: Phase name (research, plan, implement, validate, review, deliver).

        Raises:
            ValueError: If phase is not recognized.
        """
        field = self._PHASE_FIELDS.get(phase)
        if field is None:
            msg = f"Unknown phase: {phase!r}. Valid: {list(self._PHASE_FIELDS)}"
            raise ValueError(msg)
        return float(getattr(self, field))
