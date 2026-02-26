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
from trw_mcp.scoring import enforce_tier_distribution, rank_by_utility
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.analytics import (
    generate_learning_id,
    save_learning_entry,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.memory_adapter import (
    list_active_learnings,
    recall_learnings as adapter_recall,
    store_learning as adapter_store,
    update_access_tracking as adapter_update_access,
    update_learning as adapter_update,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import (
    collect_context,
    search_patterns,
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
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        _writer.ensure_dir(entries_dir)

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

        # Fetch active learnings once — reused by soft-cap check and forced distribution
        all_active: list[dict[str, object]] = []
        try:
            all_active = list_active_learnings(trw_dir)
        except Exception:
            pass  # Fail-open: listing failure must not block learning recording

        # Forced distribution soft-cap check (PRD-CORE-034-FR01)
        distribution_soft_cap_warning: str | None = None
        try:
            high_count = sum(1 for e in all_active if float(str(e.get("impact", 0.5))) >= 0.8)
            total = len(all_active)
            new_total = total + 1
            new_high = high_count + (1 if calibrated_impact >= 0.8 else 0)
            threshold_pct = _config.impact_high_threshold_pct
            threshold_frac = threshold_pct / 100.0

            if new_total >= 5 and new_total > 0 and (new_high / new_total) > threshold_frac:
                adjusted = calibrated_impact
                while adjusted >= 0.8 and new_total > 0 and (new_high / new_total) > threshold_frac:
                    adjusted *= 0.9
                    if adjusted < 0.8:
                        new_high = high_count
                    if adjusted < 0.5:
                        adjusted = 0.5
                        break
                if adjusted != calibrated_impact:
                    distribution_soft_cap_warning = (
                        f"Impact soft-capped from {calibrated_impact:.2f} to {adjusted:.2f}: "
                        f"high-impact entries ({high_count}/{total} active) would exceed "
                        f"{threshold_pct}% threshold."
                    )
                    calibrated_impact = round(adjusted, 4)
        except Exception:
            pass  # Fail-open: distribution check must not block learning recording

        learning_id = generate_learning_id()

        # Semantic dedup check (PRD-CORE-042) — must run BEFORE storing
        if _config.dedup_enabled:
            try:
                from trw_mcp.state.dedup import check_duplicate, merge_entries
                dedup_result = check_duplicate(
                    summary, detail, entries_dir, _reader, config=_config,
                )

                if dedup_result.action == "skip":
                    logger.info(
                        "learning_dedup_skipped",
                        new_id=learning_id,
                        existing_id=dedup_result.existing_id,
                        similarity=dedup_result.similarity,
                    )
                    return {
                        "status": "skipped",
                        "learning_id": learning_id,
                        "duplicate_of": dedup_result.existing_id or "",
                        "similarity": round(dedup_result.similarity, 3),
                        "message": f"Near-identical entry already exists: {dedup_result.existing_id}",
                    }
                elif dedup_result.action == "merge":
                    # Find existing file and merge
                    for yaml_file in sorted(entries_dir.glob("*.yaml")):
                        if yaml_file.name == "index.yaml":
                            continue
                        try:
                            data = _reader.read_yaml(yaml_file)
                            if str(data.get("id", "")) == dedup_result.existing_id:
                                from trw_mcp.state.persistence import model_to_dict
                                from trw_mcp.models.learning import LearningEntry
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

        # Store via SQLite adapter (primary path) — after dedup to avoid orphans
        adapter_store(
            trw_dir,
            learning_id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags or [],
            evidence=evidence or [],
            impact=calibrated_impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
        )

        # Save YAML backup via analytics (dual-write for rollback safety)
        try:
            from trw_mcp.models.learning import LearningEntry
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
            entry_path = save_learning_entry(trw_dir, entry)
            update_analytics(trw_dir, 1)
        except Exception:  # noqa: BLE001
            entry_path = entries_dir / f"{learning_id}.yaml"

        # Forced distribution enforcement (PRD-CORE-034)
        distribution_warning = ""
        demoted_ids: list[str] = []
        if _config.impact_forced_distribution_enabled and impact >= 0.7:
            try:
                # Append newly stored entry so forced distribution sees it
                all_active.append({"id": learning_id, "impact": calibrated_impact})
                all_entries: list[tuple[str, float]] = []
                for e in all_active:
                    lid = str(e.get("id", ""))
                    sc = float(str(e.get("impact", 0.5)))
                    if lid:
                        all_entries.append((lid, sc))

                demotions = enforce_tier_distribution(all_entries)
                for demoted_id, new_score in demotions:
                    demoted_ids.append(demoted_id)
                    try:
                        adapter_update(trw_dir, demoted_id, impact=new_score)
                    except Exception:
                        pass

                if demotions:
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
        result_dict: dict[str, object] = {
            "learning_id": learning_id,
            "path": str(entry_path),
            "status": "recorded",
            "distribution_warning": distribution_warning,
        }
        if distribution_soft_cap_warning:
            result_dict["soft_cap_warning"] = distribution_soft_cap_warning
        return result_dict

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
        trw_dir = resolve_trw_dir()
        result = adapter_update(
            trw_dir,
            learning_id=learning_id,
            status=status,
            detail=detail,
            impact=impact,
            summary=summary,
        )

        # Dual-write: also update YAML backup for rollback safety
        if result.get("status") == "updated":
            try:
                from datetime import date as date_type
                from trw_mcp.state.analytics import find_entry_by_id, resync_learning_index
                entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
                found = find_entry_by_id(entries_dir, learning_id)
                if found is not None:
                    entry_path, data = found
                    if status is not None:
                        data["status"] = status
                        if status in ("resolved", "obsolete"):
                            data["resolved_at"] = date_type.today().isoformat()
                    if detail is not None:
                        data["detail"] = detail
                    if summary is not None:
                        data["summary"] = summary
                    if impact is not None:
                        data["impact"] = impact
                    data["updated"] = date_type.today().isoformat()
                    _writer.write_yaml(entry_path, data)
                    resync_learning_index(trw_dir)
            except Exception:  # noqa: BLE001
                pass  # Fail-open: YAML backup update is best-effort

        return result

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

        Searches the learning store by keyword, tags, and impact score. Results are
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
        trw_dir = resolve_trw_dir()
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()
        use_compact = compact if compact is not None else is_wildcard

        # Search entries via SQLite adapter (returns list of dicts directly)
        matching_learnings = adapter_recall(
            trw_dir, query=query, tags=tags, min_impact=min_impact,
            status=status, max_results=0, compact=False,  # get all, we rank locally
        )

        # Update access tracking for recalled IDs
        matched_ids = [str(e.get("id", "")) for e in matching_learnings if e.get("id")]
        adapter_update_access(trw_dir, matched_ids)
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
