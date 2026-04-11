"""Analytics TypedDicts — run analysis, aggregates, report, impact distribution."""

from __future__ import annotations

from typing_extensions import TypedDict


class TierDistribution(TypedDict):
    """Counts per impact tier from ``assign_impact_tiers()``."""

    critical: int
    high: int
    medium: int
    low: int


class ImpactTierInfo(TypedDict):
    """Count and percentage for a single impact tier bucket."""

    count: int
    pct: float


class ImpactDistributionResult(TypedDict):
    """Return shape of ``compute_impact_distribution()``."""

    total_active: int
    critical: ImpactTierInfo
    high: ImpactTierInfo
    medium: ImpactTierInfo
    low: ImpactTierInfo


class RunAnalysisResult(TypedDict, total=False):
    """Return shape of ``_analyze_single_run()``.

    All keys are present in practice except ``complexity_class``, which is
    only included when the run.yaml has a ``complexity_class`` field set.
    ``score`` may be ``None`` when ceremony scoring raises an exception.
    """

    run_id: str
    started_at: str
    task: str
    status: str
    phase: str
    score: int | None
    session_start: bool
    deliver: bool
    checkpoint_count: int
    learn_count: int
    build_check: bool
    build_passed: bool | None
    complexity_class: str
    audit_cycles: dict[str, int]
    first_pass_compliance: dict[str, bool]


class CeremonyTrendItem(TypedDict):
    """Single entry in ``AggregateMetrics.ceremony_trend``."""

    run_id: str
    score: int
    started_at: str


class TierMetrics(TypedDict):
    """Per-tier metrics in ``AggregateMetrics.ceremony_by_tier``."""

    count: int
    avg_score: float
    pass_rate: float


class AggregateMetrics(TypedDict):
    """Return shape of ``_compute_aggregates()``."""

    total_runs: int
    avg_ceremony_score: float
    build_pass_rate: float
    avg_learnings_per_run: float
    ceremony_trend: list[CeremonyTrendItem]
    ceremony_by_tier: dict[str, TierMetrics]
    sprint_avg_audit_cycles: float
    sprint_first_pass_compliance_rate: float


class AnalyticsReport(TypedDict):
    """Return shape of ``scan_all_runs()`` and ``_empty_report()``."""

    runs: list[RunAnalysisResult]
    aggregate: AggregateMetrics
    generated_at: str
    runs_scanned: int
    parse_errors: list[str]


class RecallStats(TypedDict):
    """Return shape of ``get_recall_stats()``."""

    total_recalls: int
    unique_learnings: int
    positive_outcomes: int
    negative_outcomes: int
    neutral_outcomes: int


class EmbedHealthStatus(TypedDict):
    """Return shape of ``check_embeddings_status()``."""

    enabled: bool
    available: bool
    advisory: str
    recent_failures: int


class BatchDedupResult(TypedDict, total=False):
    """Return shape of ``batch_dedup()``.

    ``status`` is always present.

    Success path::

        {"status": "completed", "entries_scanned": int, "entries_merged": int,
         "entries_skipped": int}

    Skipped / unavailable path::

        {"status": "skipped", "reason": str}
    """

    status: str
    reason: str
    entries_scanned: int
    entries_merged: int
    entries_skipped: int
