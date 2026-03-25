"""Export/import TypedDicts (export.py)."""

from __future__ import annotations

from typing_extensions import TypedDict

from trw_mcp.models.typed_dicts._analytics import AggregateMetrics, RunAnalysisResult
from trw_mcp.models.typed_dicts._learning import LearningEntryDict


class ExportRunsSection(TypedDict):
    """Return shape of ``_collect_runs()``.

    Wraps the ``AnalyticsReport`` returned by ``scan_all_runs()``.  All keys
    mirror those of ``AnalyticsReport`` so the two types are structurally
    compatible.
    """

    runs: list[RunAnalysisResult]
    aggregate: AggregateMetrics
    generated_at: str
    runs_scanned: int
    parse_errors: list[str]


class ExportAnalyticsSection(TypedDict, total=False):
    """Return shape of ``_collect_analytics()``.

    Keys are present only when the corresponding data source exists in the
    project's ``.trw/`` directory.
    """

    session_analytics: dict[str, object]
    reflection_quality: dict[str, object]
    ceremony_aggregates: dict[str, object]


class ExportPatternsSection(TypedDict, total=False):
    """Return shape of ``_patterns_section()``.

    Contains high-frequency pattern / tag clusters extracted from learnings.
    Keys are present only when the project has enough entries to compute them.
    """

    top_tags: list[dict[str, object]]
    tag_cooccurrence: dict[str, list[str]]
    impact_by_tag: dict[str, float]
    total_entries: int


class ExportMetadata(TypedDict, total=False):
    """Metadata sub-dict embedded in ``ExportSummary``."""

    project: str
    export_date: str
    trw_version: str
    scope: str
    format: str
    learnings_count: int


class ExportSummary(TypedDict, total=False):
    """Return shape of ``export_data()`` (the full export summary).

    ``status`` and ``metadata`` are always present.  The remaining keys are
    present depending on the *scope* argument passed to ``export_data()``.
    """

    status: str
    metadata: ExportMetadata
    learnings: list[LearningEntryDict]
    learnings_csv: str
    runs: ExportRunsSection
    analytics: ExportAnalyticsSection
    error: str


class SyncIndexMdResult(TypedDict):
    """Return shape of ``sync_index_md()``.

    All keys are always present — counts reflect the PRD files scanned.
    Note: distinct from ``IndexSyncResult`` which covers the higher-level
    ``_do_index_sync()`` delivery-pipeline step.
    """

    index_path: str
    total_prds: int
    done: int
    merged: int
    review: int
    deprecated: int
    draft: int


class RoadmapSyncResult(TypedDict):
    """Return shape of ``sync_roadmap_md()``.

    Minimal result — only total count is returned alongside the path.
    """

    roadmap_path: str
    total_prds: int


class ImportLearningsResult(TypedDict, total=False):
    """Return shape of ``import_learnings()``.

    ``imported``, ``skipped_duplicate``, ``skipped_filter``, ``total_source``,
    ``imported_ids``, ``dry_run``, ``source_project``, and ``status`` are
    present on the success path.  ``error`` and ``status="failed"`` are the
    only keys present on the early-exit (no .trw / bad source file) path.
    """

    imported: int
    skipped_duplicate: int
    skipped_filter: int
    total_source: int
    imported_ids: list[str]
    dry_run: bool
    source_project: str
    status: str
    error: str
