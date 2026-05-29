"""Audit-report markdown formatters — extracted from audit.py for module-size compliance.

Belongs to the ``audit.py`` facade. Re-exports ``format_markdown`` so callers
that import via ``from trw_mcp.audit import format_markdown`` continue to work.
"""

from __future__ import annotations

from trw_mcp.models.typed_dicts import AuditReport


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
        lines.extend(
            f"| {bucket} (>={'0.7' if bucket == 'high' else '0.4' if bucket == 'medium' else '0'}) | {by_impact.get(bucket, 0)} |"
            for bucket in ("high", "medium", "low")
        )
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
        lines.extend(
            f"- {pair.get('older_id')} ↔ {pair.get('newer_id')} (similarity: {pair.get('similarity')})"
            for pair in pairs
            if isinstance(pair, dict)
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
        lines.extend(f"  - `{q}`" for q in zero_queries)
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
        lines.extend(f"  - {h}" for h in outdated)
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


def format_markdown(audit: AuditReport) -> str:
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
