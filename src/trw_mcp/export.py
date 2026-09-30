"""Cross-project export and import — learnings, runs, analytics.

CLI entry points:
- ``trw-mcp export [target_dir] --scope learnings|runs|analytics|all [--format json|csv]``
- ``trw-mcp import-learnings <source_file> [target_dir] [--min-impact 0.7] [--dry-run]``
"""

from __future__ import annotations

import contextlib
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.export_import import format_import_summary as format_import_summary
from trw_mcp.export_import import import_learnings as import_learnings
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import (
    ExportAnalyticsSection,
    ExportMetadata,
    ExportRunsSection,
    ExportSummary,
    LearningEntryDict,
)
from trw_mcp.state._helpers import load_project_config as _load_project_config
from trw_mcp.state._project_root_binding import project_bound
from trw_mcp.state.analytics import (
    compute_reflection_quality,
)
from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _package_version() -> str:
    """The trw-mcp package version that wrote the export (not the framework document's version)."""
    from trw_mcp import __version__

    return __version__


def _collect_learnings(
    trw_dir: Path,
    _config: TRWConfig,
    *,
    min_impact: float = 0.0,
    since: str | None = None,
) -> list[LearningEntryDict]:
    """Read this checkout's learning entries through its store (PRD-CORE-280 FR05).

    Replaces the pre-280 ``learnings/entries/*.yaml`` reader: ``selected_store``
    is the one place trw-mcp reaches memory, so a row synced in from another
    project (never written to this checkout's YAML mirror) is exported too. Each
    row carries ``namespace``, ``origin_project`` and ``remote_id`` alongside the
    existing learning fields.
    """
    from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict, is_system_canary
    from trw_mcp.state._origin_project import ORIGIN_PROJECT_KEY
    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    store, project_namespace = selected_store(trw_dir)
    results: list[LearningEntryDict] = []
    for namespace in (project_namespace, USER_NAMESPACE):
        # Every status, as the YAML export had: a row carries its own status. The store
        # has no offset, so the limit grows until a call comes back short.
        limit = DEFAULT_LIST_LIMIT
        while len(entries := store.list_entries(namespace, limit=limit)) == limit:
            limit *= 2
        for entry in entries:
            if is_system_canary(entry):  # tamper-detection decoys never leave in an export
                continue
            if entry.importance < min_impact:
                continue
            created = entry.created_at.date().isoformat() if entry.created_at else ""
            if since and created < since:
                continue
            row = cast("dict[str, object]", _memory_to_learning_dict(entry))
            row["namespace"] = entry.namespace
            row["origin_project"] = entry.metadata.get(ORIGIN_PROJECT_KEY, "")
            row["remote_id"] = entry.remote_id
            results.append(cast("LearningEntryDict", row))
    return results


def _learnings_to_csv(entries: list[LearningEntryDict]) -> str:
    """Convert learning entries to CSV string."""
    output = io.StringIO()
    fieldnames = [
        "id",
        "summary",
        "impact",
        "status",
        "tags",
        "access_count",
        "source_type",
        "client_profile",
        "model_id",
        "created",
        "updated",
        "namespace",
        "origin_project",
        "remote_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        tags = entry.get("tags", [])
        row = {
            "id": str(entry.get("id", "")),
            "summary": str(entry.get("summary", "")),
            "impact": str(entry.get("impact", "")),
            "status": str(entry.get("status", "")),
            "tags": ";".join(str(t) for t in tags) if isinstance(tags, list) else "",
            "access_count": str(entry.get("access_count", "")),
            "source_type": str(entry.get("source_type", "")),
            "client_profile": str(entry.get("client_profile", "")),
            "model_id": str(entry.get("model_id", "")),
            "created": str(entry.get("created", "")),
            "updated": str(entry.get("updated", "")),
            **{key: str(entry.get(key) or "") for key in ("namespace", "origin_project", "remote_id")},
        }
        writer.writerow(row)
    return output.getvalue()


def _collect_runs(target_dir: Path) -> ExportRunsSection:
    """Collect all run analytics via scan_all_runs, bound to *target_dir*."""
    with project_bound(target_dir):
        return scan_all_runs()


def _collect_analytics(
    target_dir: Path,
    trw_dir: Path,
    config: TRWConfig,
) -> ExportAnalyticsSection:
    """Merge analytics.yaml, reflection quality, and ceremony aggregates."""
    analytics: ExportAnalyticsSection = {}

    reader = FileStateReader()
    # Load analytics.yaml
    analytics_path = trw_dir / config.context_dir / "analytics.yaml"
    if analytics_path.exists():
        with contextlib.suppress(OSError, StateError):
            analytics["session_analytics"] = reader.read_yaml(analytics_path)

    # Reflection quality
    try:
        with project_bound(target_dir):
            analytics["reflection_quality"] = compute_reflection_quality(trw_dir)
    except (OSError, RuntimeError, StateError, ValueError, TypeError, ZeroDivisionError):
        logger.debug("reflection_quality_compute_failed", exc_info=True)

    # Ceremony aggregates from a fresh scan (INC-121 (c): no stale analytics-report.yaml cache any more)
    try:
        aggregate = _collect_runs(target_dir).get("aggregate")
        if isinstance(aggregate, dict) and aggregate:
            analytics["ceremony_aggregates"] = cast("dict[str, object]", aggregate)
    except (OSError, StateError, ValueError):
        logger.debug("ceremony_aggregates_scan_failed", exc_info=True)

    return analytics


def export_data(
    target_dir: Path,
    scope: str,
    *,
    fmt: str = "json",
    since: str | None = None,
    min_impact: float = 0.0,
) -> ExportSummary:
    """Export TRW data from a project directory.

    Args:
        target_dir: Absolute path to the project root.
        scope: Export scope — "learnings", "runs", "analytics", or "all".
        fmt: Output format — "json" or "csv" (csv only for learnings).
        since: Optional ISO date filter (YYYY-MM-DD).
        min_impact: Minimum impact threshold for learnings.

    Returns:
        Dict with exported data and metadata.
    """
    trw_dir = target_dir / ".trw"
    if not trw_dir.is_dir():
        return {"error": f"No .trw directory found at {target_dir}", "status": "failed"}

    if fmt == "csv" and scope != "learnings":
        return {
            "error": f"--format csv exports learnings only; scope {scope!r} needs --format json",
            "status": "failed",
        }

    config = _load_project_config(trw_dir)
    metadata: ExportMetadata = {
        "project": target_dir.name,
        "export_date": datetime.now(timezone.utc).isoformat(),
        "trw_version": _package_version(),
        "scope": scope,
        "format": fmt,
    }
    result: ExportSummary = {
        "metadata": metadata,
        "status": "ok",
    }

    if scope in ("learnings", "all"):
        learnings = _collect_learnings(
            trw_dir,
            config,
            min_impact=min_impact,
            since=since,
        )
        if fmt == "csv" and scope == "learnings":
            result["learnings_csv"] = _learnings_to_csv(learnings)
        else:
            result["learnings"] = learnings
        # Update metadata counts
        metadata["learnings_count"] = len(learnings)

    if scope in ("runs", "all"):
        result["runs"] = _collect_runs(target_dir)

    if scope in ("analytics", "all"):
        result["analytics"] = _collect_analytics(target_dir, trw_dir, config)

    return result
