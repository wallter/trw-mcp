"""Cross-project export and import — learnings, runs, analytics.

CLI entry points:
- ``trw-mcp export [target_dir] --scope learnings|runs|analytics|all [--format json|csv]``
- ``trw-mcp import-learnings <source_file> [target_dir] [--min-impact 0.7] [--dry-run]``
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._helpers import load_project_config as _load_project_config
from trw_mcp.state.analytics import (
    compute_jaccard_similarity,
    compute_reflection_quality,
    generate_learning_id,
    resync_learning_index,
)
from trw_mcp.state.analytics_report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


@contextmanager
def temp_project_root(target_dir: Path) -> Generator[None, None, None]:
    """Temporarily override TRW_PROJECT_ROOT and reset config on exit."""
    old_root = os.environ.get("TRW_PROJECT_ROOT")
    try:
        os.environ["TRW_PROJECT_ROOT"] = str(target_dir)
        _reset_config()
        yield
    finally:
        if old_root is not None:
            os.environ["TRW_PROJECT_ROOT"] = old_root
        else:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        _reset_config()


def _collect_learnings(
    trw_dir: Path,
    config: TRWConfig,
    *,
    min_impact: float = 0.0,
    since: str | None = None,
) -> list[dict[str, object]]:
    """Read all learning entries from a project, with optional filters."""
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    if not entries_dir.is_dir():
        return []

    results: list[dict[str, object]] = []
    for f in sorted(entries_dir.glob("*.yaml")):
        if f.name == "index.yaml":
            continue
        try:
            data = _reader.read_yaml(f)
        except Exception:
            continue

        impact = float(str(data.get("impact", 0)))
        if impact < min_impact:
            continue

        if since:
            created = str(data.get("created", ""))
            if created < since:
                continue

        results.append(data)

    return results


def _learnings_to_csv(entries: list[dict[str, object]]) -> str:
    """Convert learning entries to CSV string."""
    output = io.StringIO()
    fieldnames = [
        "id", "summary", "impact", "status", "tags", "q_value",
        "access_count", "source_type", "created", "updated",
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
            "q_value": str(entry.get("q_value", "")),
            "access_count": str(entry.get("access_count", "")),
            "source_type": str(entry.get("source_type", "")),
            "created": str(entry.get("created", "")),
            "updated": str(entry.get("updated", "")),
        }
        writer.writerow(row)
    return output.getvalue()


def _collect_runs(target_dir: Path) -> dict[str, object]:
    """Collect all run analytics via scan_all_runs (with env override)."""
    with temp_project_root(target_dir):
        return scan_all_runs()


def _collect_analytics(
    target_dir: Path,
    trw_dir: Path,
    config: TRWConfig,
) -> dict[str, object]:
    """Merge analytics.yaml, reflection quality, and ceremony aggregates."""
    analytics: dict[str, object] = {}

    # Load analytics.yaml
    analytics_path = trw_dir / config.context_dir / "analytics.yaml"
    if analytics_path.exists():
        try:
            analytics["session_analytics"] = _reader.read_yaml(analytics_path)
        except Exception:
            pass

    # Reflection quality
    try:
        with temp_project_root(target_dir):
            analytics["reflection_quality"] = compute_reflection_quality(trw_dir)
    except Exception:
        pass

    # Ceremony aggregates (from cached report or fresh scan)
    report_path = trw_dir / config.context_dir / "analytics-report.yaml"
    if report_path.exists():
        try:
            cached = _reader.read_yaml(report_path)
            analytics["ceremony_aggregates"] = cached.get("aggregate", {})
        except Exception:
            pass

    return analytics


def export_data(
    target_dir: Path,
    scope: str,
    *,
    fmt: str = "json",
    since: str | None = None,
    min_impact: float = 0.0,
) -> dict[str, object]:
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

    config = _load_project_config(trw_dir)
    result: dict[str, object] = {
        "metadata": {
            "project": target_dir.name,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "trw_version": config.framework_version,
            "scope": scope,
            "format": fmt,
        },
        "status": "ok",
    }

    if scope in ("learnings", "all"):
        learnings = _collect_learnings(
            trw_dir, config, min_impact=min_impact, since=since,
        )
        if fmt == "csv" and scope == "learnings":
            result["learnings_csv"] = _learnings_to_csv(learnings)
        else:
            result["learnings"] = learnings
        # Update metadata counts
        meta = result["metadata"]
        if isinstance(meta, dict):
            meta["learnings_count"] = len(learnings)

    if scope in ("runs", "all"):
        result["runs"] = _collect_runs(target_dir)

    if scope in ("analytics", "all"):
        result["analytics"] = _collect_analytics(target_dir, trw_dir, config)

    return result


def import_learnings(
    source_file: Path,
    target_dir: Path,
    *,
    min_impact: float = 0.0,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Import learnings from an export file into a target project.

    Args:
        source_file: Path to exported JSON (standalone list or full export).
        target_dir: Target project directory.
        min_impact: Minimum impact threshold for import.
        tags: Optional tag filter — only import entries with at least one matching tag.
        dry_run: If True, report what would be imported without writing.

    Returns:
        Dict with import counts and status.
    """
    trw_dir = target_dir / ".trw"
    if not trw_dir.is_dir():
        return {"error": f"No .trw directory found at {target_dir}", "status": "failed"}

    config = _load_project_config(trw_dir)

    # Load source entries
    try:
        raw = json.loads(source_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"Failed to read source file: {exc}", "status": "failed"}

    # Accept either a list of learnings or a full export with "learnings" key
    if isinstance(raw, dict) and "learnings" in raw:
        source_entries = raw["learnings"]
        source_project = str(raw.get("metadata", {}).get("project", "unknown"))
    elif isinstance(raw, list):
        source_entries = raw
        source_project = "unknown"
    else:
        return {"error": "Source file must be a JSON list or export with 'learnings' key", "status": "failed"}

    if not isinstance(source_entries, list):
        return {"error": "learnings must be a list", "status": "failed"}

    # Load existing entries for dedup
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    _writer.ensure_dir(entries_dir)
    existing_summaries: list[str] = []
    for f in entries_dir.glob("*.yaml"):
        if f.name == "index.yaml":
            continue
        try:
            data = _reader.read_yaml(f)
            existing_summaries.append(str(data.get("summary", "")))
        except Exception:
            continue

    imported = 0
    skipped_filter = 0
    skipped_duplicate = 0
    imported_ids: list[str] = []

    for entry in source_entries:
        if not isinstance(entry, dict):
            continue

        # Impact filter
        impact = float(str(entry.get("impact", 0)))
        if impact < min_impact:
            skipped_filter += 1
            continue

        # Tag filter
        if tags:
            entry_tags = entry.get("tags", [])
            if not isinstance(entry_tags, list):
                entry_tags = []
            entry_tag_strs = {str(t) for t in entry_tags}
            if not entry_tag_strs.intersection(tags):
                skipped_filter += 1
                continue

        # Dedup via Jaccard similarity
        summary = str(entry.get("summary", ""))
        is_dup = False
        for existing in existing_summaries:
            if compute_jaccard_similarity(summary, existing) >= 0.8:
                is_dup = True
                break
        if is_dup:
            skipped_duplicate += 1
            continue

        if dry_run:
            imported += 1
            continue

        # Create new entry
        from trw_mcp.models.learning import LearningEntry

        new_id = generate_learning_id()
        new_entry = LearningEntry(
            id=new_id,
            summary=summary,
            detail=str(entry.get("detail", "")),
            tags=[str(t) for t in entry.get("tags", [])] if isinstance(entry.get("tags"), list) else [],
            evidence=[str(e) for e in entry.get("evidence", [])] if isinstance(entry.get("evidence"), list) else [],
            impact=impact,
            source_type="cross-project",
            source_identity=source_project,
        )

        from trw_mcp.state.analytics import save_learning_entry

        save_learning_entry(trw_dir, new_entry)
        existing_summaries.append(summary)
        imported_ids.append(new_id)
        imported += 1

    # Resync index after imports
    if not dry_run and imported > 0:
        with temp_project_root(target_dir):
            resync_learning_index(trw_dir)

    return {
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_filter": skipped_filter,
        "total_source": len(source_entries),
        "imported_ids": imported_ids,
        "dry_run": dry_run,
        "source_project": source_project,
        "status": "ok",
    }
