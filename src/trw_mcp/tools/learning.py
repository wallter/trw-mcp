"""TRW self-learning tools — learn, recall, claude_md_sync.

These 3 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
The ``anthropic`` SDK (optional [ai] dependency) provides LLM-augmented
behavior for several tools (better summaries, relevance classification).
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import get_config
from trw_mcp.models.learning import LearningEntry
from trw_mcp.scoring import compute_impact_distribution, enforce_tier_distribution, rank_by_utility
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.analytics import (
    find_entry_by_id,
    generate_learning_id,
    resync_learning_index,
    save_learning_entry,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import (
    collect_context,
    search_entries,
    search_patterns,
    update_access_tracking,
)
from trw_mcp.state.receipts import log_recall_receipt
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()


_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()
_llm_usage_path: Path | None = None
if _config.llm_usage_log_enabled:
    _trw_dir = resolve_trw_dir()
    _llm_usage_path = _trw_dir / _config.logs_dir / _config.llm_usage_log_file
_llm = LLMClient(model=_config.llm_default_model, usage_log_path=_llm_usage_path)


def _entries_path() -> tuple[Path, Path]:
    """Resolve .trw dir and ensure the entries directory exists.

    Returns:
        Tuple of (trw_dir, entries_dir).
    """
    trw_dir = resolve_trw_dir()
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    _writer.ensure_dir(entries_dir)
    return trw_dir, entries_dir


def register_learning_tools(server: FastMCP) -> None:
    """Register self-learning tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_learn(
        summary: str,
        detail: str,
        tags: list[str] | None = None,
        evidence: list[str] | None = None,
        impact: float = 0.5,
        shard_id: str | None = None,
        source_type: str = "agent",
        source_identity: str = "",
    ) -> dict[str, object]:
        """Save a discovery so no future agent repeats your mistakes — this is how institutional knowledge grows.

        Writes a learning entry to .trw/learnings/ with utility scoring. High-impact
        learnings (>=0.7) get promoted into CLAUDE.md during delivery, becoming
        permanent project knowledge that every session loads automatically.

        Args:
            summary: One-line summary — this appears in CLAUDE.md when promoted.
            detail: Full context including what you tried, what failed, and what worked.
            tags: Categorization tags (e.g., ["testing", "gotcha"]) for filtered recall.
            evidence: Supporting evidence (file paths, error messages) that validates the learning.
            impact: Impact score 0.0-1.0 — learnings at 0.7+ get promoted to CLAUDE.md.
            shard_id: Optional shard identifier for sub-agent attribution.
            source_type: Learning provenance — "human" or "agent".
            source_identity: Name of source (e.g., "Tyler", "claude-opus-4-6").
        """
        trw_dir, _ = _entries_path()

        # One-time batch dedup migration (PRD-CORE-042 FR05)
        if _config.dedup_enabled:
            try:
                from trw_mcp.state.dedup import batch_dedup, is_migration_needed
                if is_migration_needed(trw_dir):
                    batch_dedup(trw_dir, _reader, _writer, config=_config)
            except Exception:  # noqa: BLE001
                pass  # Migration is best-effort

        # Bayesian calibration of impact score using recall tracking stats (PRD-CORE-034)
        calibrated_impact = impact
        try:
            from trw_mcp.scoring import bayesian_calibrate, compute_calibration_accuracy
            from trw_mcp.state.recall_tracking import get_recall_stats
            recall_stats = get_recall_stats()
            user_weight = compute_calibration_accuracy(recall_stats)
            calibrated_impact = bayesian_calibrate(
                user_impact=impact,
                user_weight=user_weight,
            )
        except Exception:
            pass  # Fail-open: calibration failure falls back to raw impact

        learning_id = generate_learning_id()
        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags or [],
            evidence=evidence or [],
            impact=calibrated_impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
        )

        # Semantic dedup check (PRD-CORE-042)
        if _config.dedup_enabled:
            try:
                from trw_mcp.state.dedup import check_duplicate, merge_entries
                _, entries_dir = _entries_path()
                dedup_result = check_duplicate(
                    summary, detail, entries_dir, _reader, config=_config,
                )

                if dedup_result.action == "skip":
                    # FR04: Update access_count and recurrence on existing entry
                    dedup_existing_id = dedup_result.existing_id or ""
                    _, entries_dir = _entries_path()
                    for yaml_file in sorted(entries_dir.glob("*.yaml")):
                        if yaml_file.name == "index.yaml":
                            continue
                        try:
                            data = _reader.read_yaml(yaml_file)
                            if str(data.get("id", "")) == dedup_existing_id:
                                from datetime import date as _date
                                data["access_count"] = int(str(data.get("access_count", 0))) + 1
                                data["recurrence"] = int(str(data.get("recurrence", 1))) + 1
                                data["updated"] = _date.today().isoformat()
                                _writer.write_yaml(yaml_file, data)
                                break
                        except Exception:  # noqa: BLE001
                            continue
                    logger.info(
                        "learning_dedup_skipped",
                        new_id=learning_id,
                        existing_id=dedup_existing_id,
                        similarity=dedup_result.similarity,
                    )
                    return {
                        "status": "skipped",
                        "learning_id": learning_id,
                        "duplicate_of": dedup_existing_id,
                        "similarity": round(dedup_result.similarity, 3),
                        "message": f"Near-identical entry already exists: {dedup_existing_id}",
                    }
                elif dedup_result.action == "merge":
                    # Find the existing entry file and merge into it
                    for yaml_file in sorted(entries_dir.glob("*.yaml")):
                        if yaml_file.name == "index.yaml":
                            continue
                        try:
                            data = _reader.read_yaml(yaml_file)
                            if str(data.get("id", "")) == dedup_result.existing_id:
                                from trw_mcp.state.persistence import model_to_dict
                                merge_entries(yaml_file, model_to_dict(entry), _reader, _writer)
                                logger.info(
                                    "learning_dedup_merged",
                                    new_id=learning_id,
                                    existing_id=dedup_result.existing_id,
                                    similarity=dedup_result.similarity,
                                )
                                return {
                                    "status": "merged",
                                    "merged_into": dedup_result.existing_id or "",
                                    "new_id": learning_id,
                                    "similarity": str(round(dedup_result.similarity, 3)),
                                    "message": f"Merged into existing entry: {dedup_result.existing_id}",
                                }
                        except Exception:  # noqa: BLE001
                            continue
            except Exception as exc:  # noqa: BLE001
                logger.debug("dedup_check_failed", error=str(exc))
                # Fall through to normal save on any dedup failure

        entry_path = save_learning_entry(trw_dir, entry)
        update_analytics(trw_dir, 1)

        # Forced distribution enforcement (PRD-CORE-034)
        # Only runs when the new learning is in a high/critical tier, matching
        # the original advisory check threshold (impact >= 0.7).
        distribution_warning = ""
        demoted_ids: list[str] = []
        if _config.impact_forced_distribution_enabled and impact >= 0.7:
            try:
                _, entries_dir = _entries_path()
                # Build (id, impact) list of all active entries including the new one
                all_entries: list[tuple[str, float]] = []
                from trw_mcp.state.analytics import find_entry_by_id
                for yaml_file in sorted(entries_dir.glob("*.yaml")):
                    try:
                        data = _reader.read_yaml(yaml_file)
                    except Exception:
                        continue
                    if str(data.get("status", "active")) != "active":
                        continue
                    lid = str(data.get("id", ""))
                    sc = float(str(data.get("impact", 0.5)))
                    if lid:
                        all_entries.append((lid, sc))

                demotions = enforce_tier_distribution(all_entries)
                for demoted_id, new_score in demotions:
                    demoted_ids.append(demoted_id)
                    # Find and update the demoted entry on disk
                    found = find_entry_by_id(entries_dir, demoted_id)
                    if found is not None:
                        entry_path_dem, data_dem = found
                        data_dem["impact"] = new_score
                        from datetime import date as _date
                        data_dem["updated"] = _date.today().isoformat()
                        _writer.write_yaml(entry_path_dem, data_dem)

                if demotions:
                    # Use raw impact for tier name in warning (user-facing label)
                    tier_name = "critical" if impact >= 0.9 else "high"
                    distribution_warning = (
                        f"Impact tier '{tier_name}' exceeded cap. "
                        f"Forced distribution: demoted {len(demotions)} entr"
                        f"{'y' if len(demotions) == 1 else 'ies'} to maintain tier caps. "
                        f"IDs: {[d[0] for d in demotions]}"
                    )
            except Exception:
                pass  # Fail-open: distribution enforcement must not block learning recording

        logger.info("trw_learn_recorded", learning_id=learning_id, summary=summary, impact=impact)
        return {
            "learning_id": learning_id,
            "path": str(entry_path),
            "status": "recorded",
            "distribution_warning": distribution_warning,
        }

    @server.tool()
    @log_tool_call
    def trw_learn_update(
        learning_id: str,
        status: str | None = None,
        detail: str | None = None,
        impact: float | None = None,
        summary: str | None = None,
    ) -> dict[str, str]:
        """Keep your knowledge base accurate — mark resolved issues, retire obsolete gotchas, or refine details.

        Stale learnings waste attention budget. Use this to mark learnings as resolved
        (issue was fixed) or obsolete (no longer applicable), or to refine the
        detail/summary with better information discovered during implementation.

        Args:
            learning_id: ID of the learning to update (e.g., "L-abc12345").
            status: New status — "active", "resolved", or "obsolete". Resolved/obsolete entries stop appearing in recall.
            detail: Updated detail text (replaces existing detail).
            impact: Updated impact score (0.0-1.0).
            summary: Updated summary text (replaces existing summary).
        """
        from datetime import date as date_type

        trw_dir, entries_dir = _entries_path()

        result = find_entry_by_id(entries_dir, learning_id)
        if result is None:
            return {"error": f"Learning {learning_id} not found", "status": "not_found"}

        entry_path, data = result
        changes: list[str] = []

        if status is not None:
            valid_statuses = {"active", "resolved", "obsolete"}
            if status not in valid_statuses:
                return {"error": f"Invalid status '{status}'. Must be one of: {valid_statuses}", "status": "invalid"}
            data["status"] = status
            changes.append(f"status→{status}")
            if status in ("resolved", "obsolete"):
                data["resolved_at"] = date_type.today().isoformat()

        if detail is not None:
            data["detail"] = detail
            changes.append("detail updated")

        if summary is not None:
            data["summary"] = summary
            changes.append("summary updated")

        if impact is not None:
            if not 0.0 <= impact <= 1.0:
                return {"error": f"Impact must be 0.0-1.0, got {impact}", "status": "invalid"}
            data["impact"] = impact
            changes.append(f"impact→{impact}")

        if not changes:
            return {"learning_id": learning_id, "status": "no_changes"}

        data["updated"] = date_type.today().isoformat()
        _writer.write_yaml(entry_path, data)
        resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_updated",
            learning_id=learning_id,
            changes=changes,
        )
        return {
            "learning_id": learning_id,
            "changes": ", ".join(changes),
            "status": "updated",
        }

    @server.tool()
    @log_tool_call
    def trw_recall(
        query: str,
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = None,
        shard_id: str | None = None,
        max_results: int = _config.recall_max_results,
        compact: bool | None = None,
    ) -> dict[str, object]:
        """Retrieve prior learnings relevant to your current task — avoid re-discovering what is already known.

        Searches .trw/learnings/ by keyword, tags, and impact score. Results are
        ranked by utility (impact x recency x relevance). Use this before starting
        work on an unfamiliar area to load existing project knowledge.

        Args:
            query: Search query (keywords matched against summaries/details).
                Use "*" to list all (auto-enables compact mode).
            tags: Optional tag filter — only return entries matching these tags.
            min_impact: Minimum impact score filter (0.0-1.0). Use 0.7 for high-impact only.
            status: Optional status filter — 'active', 'resolved', or 'obsolete'.
            shard_id: Optional shard identifier for receipt attribution.
            max_results: Maximum learnings to return (default 25, 0 = unlimited).
            compact: When True, return only essential fields per learning.
                When None (default), auto-enables for wildcard queries.
        """
        trw_dir, entries_dir = _entries_path()
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()
        use_compact = compact if compact is not None else is_wildcard

        # Search entries and update access tracking
        matching_learnings, matched_files = search_entries(
            entries_dir, query_tokens, _reader,
            tags=tags, min_impact=min_impact, status=status,
        )
        matched_ids = update_access_tracking(matched_files, _reader, _writer)
        log_recall_receipt(trw_dir, query, matched_ids, shard_id=shard_id)

        # Track each recalled learning for outcome-based calibration (PRD-CORE-034)
        try:
            from trw_mcp.state.recall_tracking import record_recall as _record_recall
            for lid in matched_ids:
                _record_recall(lid, query)
        except Exception:
            pass  # Fail-open: tracking failure must not affect tool result

        # Augment local results with remote shared learnings (PRD-CORE-033)
        try:
            from trw_mcp.telemetry.remote_recall import fetch_shared_learnings
            remote = fetch_shared_learnings(query)
            if remote:
                matching_learnings = list(matching_learnings) + remote
        except Exception:
            pass  # Fail-open: remote recall failure must not affect local results

        # Search patterns and rank all results by utility
        matching_patterns = search_patterns(
            trw_dir / _config.patterns_dir, query_tokens, _reader,
        )
        ranked_learnings = rank_by_utility(
            matching_learnings, query_tokens, _config.recall_utility_lambda,
        )

        # Capture pre-cap counts for the total_available response field
        total_available = len(ranked_learnings) + len(matching_patterns)

        # Apply result cap
        if max_results > 0:
            ranked_learnings = ranked_learnings[:max_results]

        # Strip to compact fields when requested
        if use_compact:
            allowed = _config.recall_compact_fields
            ranked_learnings = [
                {k: v for k, v in entry.items() if k in allowed}
                for entry in ranked_learnings
            ]

        # Skip context collection for compact wildcard queries (saves I/O)
        context_data: dict[str, object] = {}
        if not (is_wildcard and use_compact):
            context_data = collect_context(trw_dir, _config.context_dir, _reader)

        logger.info(
            "trw_recall_searched",
            query=query,
            learnings_found=len(ranked_learnings),
            patterns_found=len(matching_patterns),
            compact=use_compact,
        )

        return {
            "query": query,
            "learnings": ranked_learnings,
            "patterns": matching_patterns,
            "context": context_data,
            "total_matches": len(ranked_learnings) + len(matching_patterns),
            "total_available": total_available,
            "compact": use_compact,
            "max_results": max_results,
        }

    @server.tool()
    @log_tool_call
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
    ) -> dict[str, object]:
        """Promote your best learnings into CLAUDE.md — the next session starts with your insights built in.

        Renders high-impact learnings, behavioral protocol, ceremony guidance, and
        patterns into the auto-generated CLAUDE.md section. This is how individual
        session discoveries become permanent project instructions.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
        """
        return execute_claude_md_sync(scope, target_dir, _config, _reader, _writer, _llm)
