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
from pathlib import Path

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._helpers import load_project_config as _load_project_config
from trw_mcp.state.analytics import (
    apply_status_update,
    auto_prune_excess_entries,
    compute_reflection_quality,
    find_duplicate_learnings,
    resync_learning_index,
)
from trw_mcp.state.analytics_report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader

# Thresholds for PASS/WARN/FAIL verdicts
_BLOAT_WARN_PCT = 0.20
_CEREMONY_PASS_SCORE = 50
_RECALL_MISS_WARN_PCT = 0.25


def _iter_entries(entries_dir: Path) -> list[dict[str, object]]:
    """Read all YAML entries from a directory (skipping index.yaml)."""
    from trw_mcp.state._helpers import iter_yaml_entry_files

    reader = FileStateReader()
    entries: list[dict[str, object]] = []
    for f in iter_yaml_entry_files(entries_dir):
        try:
            entries.append(reader.read_yaml(f))
        except Exception:
            continue
    return entries


# ---------------------------------------------------------------------------
# Audit section functions
# ---------------------------------------------------------------------------


def _audit_learnings(
    entries: list[dict[str, object]],
) -> dict[str, object]:
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

    return {
        "total": total,
        "by_status": dict(status_counts),
        "by_impact": impact_buckets,
        "top_tags": tag_counts.most_common(10),
        "by_source": dict(source_counts),
        "telemetry_bloat": {
            "count": bloat_count,
            "pct": round(bloat_pct, 3),
            "verdict": "WARN" if bloat_pct > _BLOAT_WARN_PCT else "PASS",
        },
    }


def _audit_duplicates(entries_dir: Path) -> dict[str, object]:
    """Duplicate detection via Jaccard similarity."""
    duplicates = find_duplicate_learnings(entries_dir, threshold=0.8)
    return {
        "pairs": [
            {"older_id": o, "newer_id": n, "similarity": s}
            for o, n, s in duplicates
        ],
        "count": len(duplicates),
        "verdict": "WARN" if duplicates else "PASS",
    }


def _audit_index_consistency(
    trw_dir: Path,
    config: TRWConfig,
    actual_count: int,
) -> dict[str, object]:
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
) -> dict[str, object]:
    """Parse recall_log.jsonl for query effectiveness stats."""
    receipt_path = (
        trw_dir / config.learnings_dir / config.receipts_dir / "recall_log.jsonl"
    )
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
    except Exception:
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
) -> dict[str, object]:
    """Cross-run ceremony compliance via scan_all_runs (with env override)."""
    old_root = os.environ.get("TRW_PROJECT_ROOT")
    try:
        os.environ["TRW_PROJECT_ROOT"] = str(target_dir)
        _reset_config()
        result = scan_all_runs()
    finally:
        if old_root is not None:
            os.environ["TRW_PROJECT_ROOT"] = old_root
        else:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        _reset_config()

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


def _audit_reflection_quality(trw_dir: Path) -> dict[str, object]:
    """Reflection quality metrics."""
    old_root = os.environ.get("TRW_PROJECT_ROOT")
    try:
        os.environ["TRW_PROJECT_ROOT"] = str(trw_dir.parent)
        _reset_config()
        result = compute_reflection_quality(trw_dir)
    finally:
        if old_root is not None:
            os.environ["TRW_PROJECT_ROOT"] = old_root
        else:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        _reset_config()
    return result


def _audit_hook_versions(target_dir: Path) -> dict[str, object]:
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

        deployed_hash = hashlib.sha256(
            deployed.read_bytes()
        ).hexdigest()
        bundled_hash = hashlib.sha256(
            bundled.read_bytes()
        ).hexdigest()

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
    entries: list[dict[str, object]],
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
) -> dict[str, object]:
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

    result: dict[str, object] = {
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
        fix_actions: dict[str, object] = {}

        # Retire telemetry bloat entries (PRD-FIX-021)
        retired_count = _retire_telemetry_bloat(entries, trw_dir)
        fix_actions["telemetry_bloat_retired"] = retired_count

        # Prune duplicates
        old_root = os.environ.get("TRW_PROJECT_ROOT")
        try:
            os.environ["TRW_PROJECT_ROOT"] = str(target_dir)
            _reset_config()
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
            _reset_config()

        result["fix_actions"] = fix_actions

    return result


# ---------------------------------------------------------------------------
# Output formatters — section helpers
# ---------------------------------------------------------------------------


def _format_learnings_section(learnings: object) -> list[str]:
    """Format the learnings section of the audit report."""
    if not isinstance(learnings, dict):
        return []

    lines: list[str] = []
    total = learnings.get("total", 0)
    lines.append(f"## Learnings ({total} total)")
    lines.append("")

    by_status = learnings.get("by_status", {})
    if isinstance(by_status, dict) and by_status:
        lines.append("| Status | Count | % |")
        lines.append("|--------|-------|---|")
        for status, count in sorted(by_status.items()):
            pct = round(int(str(count)) / max(int(str(total)), 1) * 100, 1)
            lines.append(f"| {status} | {count} | {pct}% |")
        lines.append("")

    by_impact = learnings.get("by_impact", {})
    if isinstance(by_impact, dict):
        lines.append("| Impact | Count |")
        lines.append("|--------|-------|")
        for bucket in ("high", "medium", "low"):
            lines.append(f"| {bucket} (>={'0.7' if bucket == 'high' else '0.4' if bucket == 'medium' else '0'}) | {by_impact.get(bucket, 0)} |")
        lines.append("")

    bloat = learnings.get("telemetry_bloat", {})
    if isinstance(bloat, dict):
        verdict = bloat.get("verdict", "")
        lines.append(
            f"Telemetry bloat: {bloat.get('count', 0)} entries "
            f"({round(float(str(bloat.get('pct', 0))) * 100, 1)}%) — **{verdict}**"
        )
        lines.append("")

    return lines


def _format_duplicates_section(dups: object) -> list[str]:
    """Format the duplicates section of the audit report."""
    if not isinstance(dups, dict):
        return []

    lines: list[str] = []
    pairs = dups.get("pairs", [])
    verdict = dups.get("verdict", "")
    lines.append(f"## Duplicates — **{verdict}**")
    if isinstance(pairs, list) and pairs:
        lines.append("")
        for pair in pairs:
            if isinstance(pair, dict):
                lines.append(
                    f"- {pair.get('older_id')} ↔ {pair.get('newer_id')} "
                    f"(similarity: {pair.get('similarity')})"
                )
    else:
        lines.append("No duplicates found.")
    lines.append("")
    return lines


def _format_index_section(idx: object) -> list[str]:
    """Format the index consistency section of the audit report."""
    if not isinstance(idx, dict):
        return []

    lines: list[str] = []
    verdict = idx.get("verdict", "")
    lines.append(f"## Index Consistency — **{verdict}**")
    if verdict == "WARN":
        lines.append(
            f"analytics.yaml reports {idx.get('analytics_total')} learnings, "
            f"but {idx.get('actual_count')} entry files exist."
        )
    elif verdict == "PASS":
        lines.append(f"Counts match: {idx.get('actual_count')} entries.")
    lines.append("")
    return lines


def _format_recall_section(recall: object) -> list[str]:
    """Format the recall effectiveness section of the audit report."""
    if not isinstance(recall, dict) or recall.get("verdict") == "SKIP":
        return []

    lines: list[str] = []
    verdict = recall.get("verdict", "")
    lines.append(f"## Recall Effectiveness — **{verdict}**")
    lines.append(f"- Total queries: {recall.get('total_queries', 0)}")
    lines.append(f"- Wildcard queries: {recall.get('wildcard_queries', 0)}")
    lines.append(f"- Named query miss rate: {round(float(str(recall.get('miss_rate', 0))) * 100, 1)}%")
    zero_queries = recall.get("top_zero_match_queries", [])
    if isinstance(zero_queries, list) and zero_queries:
        lines.append("- Top zero-match queries:")
        for q in zero_queries:
            lines.append(f"  - `{q}`")
    lines.append("")
    return lines


def _format_ceremony_section(ceremony: object) -> list[str]:
    """Format the ceremony compliance section of the audit report."""
    if not isinstance(ceremony, dict):
        return []

    lines: list[str] = []
    verdict = ceremony.get("verdict", "")
    lines.append(f"## Ceremony Compliance — **{verdict}**")
    lines.append(f"- Runs scanned: {ceremony.get('runs_scanned', 0)}")
    lines.append(f"- Avg ceremony score: {ceremony.get('avg_ceremony_score', 0)}/100")
    lines.append(f"- Build pass rate: {round(float(str(ceremony.get('build_pass_rate', 0))) * 100, 1)}%")
    lines.append("")
    return lines


def _format_reflection_section(reflection: object) -> list[str]:
    """Format the reflection quality section of the audit report."""
    if not isinstance(reflection, dict):
        return []

    lines: list[str] = []
    score = reflection.get("score", 0)
    lines.append(f"## Reflection Quality — {score}")
    components = reflection.get("components", {})
    if isinstance(components, dict):
        for name, val in components.items():
            lines.append(f"- {name}: {val}")
    lines.append("")
    return lines


def _format_hooks_section(hooks: object) -> list[str]:
    """Format the hook versions section of the audit report."""
    if not isinstance(hooks, dict) or hooks.get("verdict") == "SKIP":
        return []

    lines: list[str] = []
    verdict = hooks.get("verdict", "")
    lines.append(f"## Hook Versions — **{verdict}**")
    lines.append(f"- Total: {hooks.get('total', 0)}")
    lines.append(f"- Up to date: {hooks.get('up_to_date', 0)}")
    outdated = hooks.get("outdated", [])
    if isinstance(outdated, list) and outdated:
        lines.append("- Outdated:")
        for h in outdated:
            lines.append(f"  - {h}")
    lines.append("")
    return lines


def _format_fix_actions_section(fix_actions: object) -> list[str]:
    """Format the fix actions section of the audit report."""
    if not isinstance(fix_actions, dict):
        return []

    lines: list[str] = []
    lines.append("## Fix Actions Applied")
    retired = fix_actions.get("telemetry_bloat_retired", 0)
    if retired:
        lines.append(f"- Telemetry bloat retired: {retired} entries")
    prune = fix_actions.get("prune", {})
    if isinstance(prune, dict):
        lines.append(f"- Pruned: {prune.get('actions_taken', 0)} entries")
    if fix_actions.get("index_resynced"):
        lines.append("- Index resynced")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_markdown(audit: dict[str, object]) -> str:
    """Format audit results as a human-readable markdown report."""
    lines: list[str] = [
        f"# TRW Audit Report — {audit.get('project', 'unknown')}",
        f"Generated: {audit.get('generated_at', '')}",
        "",
    ]
    lines.extend(_format_learnings_section(audit.get("learnings", {})))
    lines.extend(_format_duplicates_section(audit.get("duplicates", {})))
    lines.extend(_format_index_section(audit.get("index_consistency", {})))
    lines.extend(_format_recall_section(audit.get("recall_effectiveness", {})))
    lines.extend(_format_ceremony_section(audit.get("ceremony_compliance", {})))
    lines.extend(_format_reflection_section(audit.get("reflection_quality", {})))
    lines.extend(_format_hooks_section(audit.get("hook_versions", {})))
    lines.extend(_format_fix_actions_section(audit.get("fix_actions")))
    return "\n".join(lines)
