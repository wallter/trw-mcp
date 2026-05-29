"""Cross-project audit — comprehensive TRW health analysis for any project directory.

CLI entry point: ``trw-mcp audit [target_dir] [--format json|markdown] [--output FILE] [--fix]``

All functions take explicit ``target_dir`` / ``trw_dir`` parameters so they work
on *any* project, not just the one the MCP server is running in.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)
from pathlib import Path
from typing import cast

from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.models.typed_dicts import (
    AuditCeremonyComplianceResult,
    AuditDuplicatePairDict,
    AuditDuplicatesResult,
    AuditFixActionsDict,
    AuditHookVersionsResult,
    AuditIndexConsistencyResult,
    AuditLearningsResult,
    AuditRecallEffectivenessResult,
    AuditReflectionQualityResult,
    AuditReport,
    AuditTelemetryBloatDict,
    LearningEntryDict,
)
from trw_mcp.state._helpers import load_project_config as _load_project_config
from trw_mcp.state.analytics import (
    apply_status_update,
    auto_prune_excess_entries,
    compute_reflection_quality,
    find_duplicate_learnings,
    resync_learning_index,
)
from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader

# Thresholds for PASS/WARN/FAIL verdicts
_BLOAT_WARN_PCT = 0.20
_CEREMONY_PASS_SCORE = 50
_RECALL_MISS_WARN_PCT = 0.25


def _iter_entries(entries_dir: Path) -> list[LearningEntryDict]:
    """Read all YAML entries from a directory (skipping index.yaml)."""
    from trw_mcp.state._helpers import iter_yaml_entry_files

    reader = FileStateReader()
    entries: list[LearningEntryDict] = []
    for f in iter_yaml_entry_files(entries_dir):
        try:
            entries.append(cast("LearningEntryDict", reader.read_yaml(f)))
        except Exception:  # per-item error handling: fail-open, skip malformed YAML entries  # noqa: S112
            continue
    return entries


# ---------------------------------------------------------------------------
# Audit section functions
# ---------------------------------------------------------------------------


def _audit_learnings(
    entries: list[LearningEntryDict],
) -> AuditLearningsResult:
    """Learning inventory: counts by status, impact, tags, source, bloat."""
    status_counts: Counter[str] = Counter()
    impact_buckets = {"high": 0, "medium": 0, "low": 0}
    tag_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    bloat_count = 0

    for entry in entries:
        status = str(entry.get("status", "active"))
        status_counts[status] += 1

        impact = float(str(entry.get("impact", 0)))
        if impact >= 0.7:
            impact_buckets["high"] += 1
        elif impact >= 0.4:
            impact_buckets["medium"] += 1
        else:
            impact_buckets["low"] += 1

        tags = entry.get("tags", [])
        if isinstance(tags, list):
            tag_counts.update(str(t) for t in tags)

        source = str(entry.get("source_type", "agent"))
        source_counts[source] += 1

        summary = str(entry.get("summary", ""))
        if summary.startswith(("Repeated operation:", "Success:")):
            bloat_count += 1

    total = len(entries)
    bloat_pct = bloat_count / max(total, 1)

    bloat: AuditTelemetryBloatDict = {
        "count": bloat_count,
        "pct": round(bloat_pct, 3),
        "verdict": "WARN" if bloat_pct > _BLOAT_WARN_PCT else "PASS",
    }
    return {
        "total": total,
        "by_status": dict(status_counts),
        "by_impact": impact_buckets,
        "top_tags": tag_counts.most_common(10),
        "by_source": dict(source_counts),
        "telemetry_bloat": bloat,
    }


def _audit_duplicates(entries_dir: Path) -> AuditDuplicatesResult:
    """Duplicate detection via Jaccard similarity."""
    duplicates = find_duplicate_learnings(entries_dir, threshold=0.8)
    pairs: list[AuditDuplicatePairDict] = [{"older_id": o, "newer_id": n, "similarity": s} for o, n, s in duplicates]
    return {
        "pairs": pairs,
        "count": len(duplicates),
        "verdict": "WARN" if duplicates else "PASS",
    }


def _audit_index_consistency(
    trw_dir: Path,
    config: TRWConfig,
    actual_count: int,
) -> AuditIndexConsistencyResult:
    """Check analytics.yaml total_learnings matches actual entry count."""
    reader = FileStateReader()
    analytics_path = trw_dir / config.context_dir / "analytics.yaml"
    if not analytics_path.exists():
        return {"analytics_total": None, "actual_count": actual_count, "verdict": "SKIP"}

    data = reader.read_yaml(analytics_path)
    analytics_total = int(str(data.get("total_learnings", 0)))
    match = analytics_total == actual_count

    return {
        "analytics_total": analytics_total,
        "actual_count": actual_count,
        "match": match,
        "verdict": "PASS" if match else "WARN",
    }


def _audit_recall_effectiveness(
    trw_dir: Path,
    config: TRWConfig,
) -> AuditRecallEffectivenessResult:
    """Parse recall_log.jsonl for query effectiveness stats."""
    receipt_path = trw_dir / config.learnings_dir / config.receipts_dir / "recall_log.jsonl"
    if not receipt_path.exists():
        return {"total_queries": 0, "verdict": "SKIP"}

    total = 0
    wildcard = 0
    zero_match = 0
    zero_match_queries: list[str] = []

    try:
        lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            query = str(record.get("query", ""))
            matched_ids = record.get("matched_ids", [])

            if query.strip() in ("*", ""):
                wildcard += 1
            elif len(matched_ids) == 0:
                zero_match += 1
                if len(zero_match_queries) < 5:
                    zero_match_queries.append(query)
    except Exception:  # justified: fail-open, corrupt recall log degrades gracefully
        return {"total_queries": 0, "verdict": "SKIP"}

    named_queries = total - wildcard
    miss_rate = zero_match / max(named_queries, 1) if named_queries > 0 else 0.0

    return {
        "total_queries": total,
        "wildcard_queries": wildcard,
        "named_queries": named_queries,
        "zero_match": zero_match,
        "miss_rate": round(miss_rate, 3),
        "top_zero_match_queries": zero_match_queries,
        "verdict": "WARN" if miss_rate > _RECALL_MISS_WARN_PCT else "PASS",
    }


def _audit_ceremony_compliance(
    target_dir: Path,
) -> AuditCeremonyComplianceResult:
    """Cross-run ceremony compliance via scan_all_runs (with env override)."""
    old_root = os.environ.get("TRW_PROJECT_ROOT")
    try:
        os.environ["TRW_PROJECT_ROOT"] = str(target_dir)
        reload_config()
        result = scan_all_runs()
    finally:
        if old_root is not None:
            os.environ["TRW_PROJECT_ROOT"] = old_root
        else:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        reload_config()

    aggregate = result.get("aggregate", {})
    if not isinstance(aggregate, dict):
        aggregate = {}

    avg_score = float(str(aggregate.get("avg_ceremony_score", 0)))
    return {
        "runs_scanned": result.get("runs_scanned", 0),
        "avg_ceremony_score": avg_score,
        "build_pass_rate": aggregate.get("build_pass_rate", 0),
        "avg_learnings_per_run": aggregate.get("avg_learnings_per_run", 0),
        "verdict": "PASS" if avg_score >= _CEREMONY_PASS_SCORE else "WARN",
    }


def _audit_reflection_quality(trw_dir: Path) -> AuditReflectionQualityResult:
    """Reflection quality metrics."""
    old_root = os.environ.get("TRW_PROJECT_ROOT")
    try:
        os.environ["TRW_PROJECT_ROOT"] = str(trw_dir.parent)
        reload_config()
        result = compute_reflection_quality(trw_dir)
    finally:
        if old_root is not None:
            os.environ["TRW_PROJECT_ROOT"] = old_root
        else:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        reload_config()
    return cast("AuditReflectionQualityResult", result)


def _audit_hook_versions(target_dir: Path) -> AuditHookVersionsResult:
    """Compare deployed hooks against bundled data files by sha256."""
    from importlib.resources import files as pkg_files

    hooks_dir = target_dir / ".claude" / "hooks"
    if not hooks_dir.is_dir():
        return {"total": 0, "up_to_date": 0, "outdated": [], "verdict": "SKIP"}

    data_hooks = pkg_files("trw_mcp.data") / "hooks"
    up_to_date = 0
    outdated: list[str] = []
    total = 0

    for deployed in sorted(hooks_dir.glob("*.sh")):
        total += 1
        bundled = data_hooks / deployed.name
        if not bundled.is_file():
            continue

        deployed_hash = hashlib.sha256(deployed.read_bytes()).hexdigest()
        bundled_hash = hashlib.sha256(bundled.read_bytes()).hexdigest()

        if deployed_hash == bundled_hash:
            up_to_date += 1
        else:
            outdated.append(deployed.name)

    return {
        "total": total,
        "up_to_date": up_to_date,
        "outdated": outdated,
        "verdict": "PASS" if not outdated else "WARN",
    }


# ---------------------------------------------------------------------------
# Fix helpers
# ---------------------------------------------------------------------------


def _retire_telemetry_bloat(
    entries: list[LearningEntryDict],
    trw_dir: Path,
) -> int:
    """Mark active telemetry-noise entries as obsolete.

    Finds entries with summaries starting with "Repeated operation:" or
    "Success:" and marks them obsolete (PRD-FIX-021).

    Args:
        entries: All learning entries from the project.
        trw_dir: Path to .trw directory.

    Returns:
        Number of entries retired.
    """
    retired = 0
    for entry in entries:
        summary = str(entry.get("summary", ""))
        status = str(entry.get("status", "active"))
        if status != "active":
            continue
        if summary.startswith(("Repeated operation:", "Success:")):
            entry_id = str(entry.get("id", ""))
            if entry_id:
                apply_status_update(trw_dir, entry_id, "obsolete")
                retired += 1
    return retired


# ---------------------------------------------------------------------------
# Main audit orchestrator
# ---------------------------------------------------------------------------


def run_audit(
    target_dir: Path,
    *,
    fix: bool = False,
) -> AuditReport:
    """Run comprehensive TRW health audit on a project directory.

    Args:
        target_dir: Absolute path to the project root.
        fix: If True, auto-prune duplicates and resync index.

    Returns:
        Structured audit results dict.
    """
    trw_dir = target_dir / ".trw"
    if not trw_dir.is_dir():
        return {"error": f"No .trw directory found at {target_dir}", "status": "failed"}

    config = _load_project_config(trw_dir)
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    entries = _iter_entries(entries_dir)

    result: AuditReport = {
        "project": target_dir.name,
        "target_dir": str(target_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "learnings": _audit_learnings(entries),
        "duplicates": _audit_duplicates(entries_dir),
        "index_consistency": _audit_index_consistency(trw_dir, config, len(entries)),
        "recall_effectiveness": _audit_recall_effectiveness(trw_dir, config),
        "ceremony_compliance": _audit_ceremony_compliance(target_dir),
        "reflection_quality": _audit_reflection_quality(trw_dir),
        "hook_versions": _audit_hook_versions(target_dir),
        "status": "ok",
    }

    if fix:
        fix_actions: AuditFixActionsDict = {}

        # Retire telemetry bloat entries (PRD-FIX-021)
        retired_count = _retire_telemetry_bloat(entries, trw_dir)
        fix_actions["telemetry_bloat_retired"] = retired_count

        # Prune duplicates
        old_root = os.environ.get("TRW_PROJECT_ROOT")
        try:
            os.environ["TRW_PROJECT_ROOT"] = str(target_dir)
            reload_config()
            prune_result = auto_prune_excess_entries(trw_dir)
            fix_actions["prune"] = prune_result

            # Resync index
            resync_learning_index(trw_dir)
            fix_actions["index_resynced"] = True
        finally:
            if old_root is not None:
                os.environ["TRW_PROJECT_ROOT"] = old_root
            else:
                os.environ.pop("TRW_PROJECT_ROOT", None)
            reload_config()

        result["fix_actions"] = fix_actions

    return result


# Output formatters extracted to _audit_format (PRD-DIST-243 batch 10).
# Re-exported for callers that import via ``from trw_mcp.audit import format_markdown``.
from trw_mcp._audit_format import format_markdown as format_markdown
