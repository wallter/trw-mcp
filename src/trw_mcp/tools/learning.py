"""TRW self-learning tools — learn, recall, claude_md_sync.

These 3 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
The ``anthropic`` SDK (optional [ai] dependency) provides LLM-augmented
behavior for several tools (better summaries, relevance classification).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.scoring import rank_by_utility
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
from trw_mcp.tools._learning_helpers import (
    LearningParams,
    calibrate_impact,
    check_and_handle_dedup,
    check_soft_cap,
    enforce_distribution,
    is_noise_summary,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def _create_llm_client() -> LLMClient:
    """Create an LLM client using current config."""
    config = get_config()
    llm_usage_path: Path | None = None
    if config.llm_usage_log_enabled:
        trw_dir = resolve_trw_dir()
        llm_usage_path = trw_dir / config.logs_dir / config.llm_usage_log_file
    return LLMClient(model=config.llm_default_model, usage_log_path=llm_usage_path)


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
        # Input validation (PRD-QUAL-042-FR06): impact bounds
        impact = max(0.0, min(1.0, impact))

        # PRD-QUAL-032-FR09: Reject auto-generated noise entries early
        if is_noise_summary(summary):
            return {
                "status": "rejected",
                "reason": "noise_filter",
                "message": f"Summary matches noise pattern — not persisted: {summary[:60]}",
            }

        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        writer.ensure_dir(entries_dir)

        # One-time batch dedup migration (PRD-CORE-042 FR05)
        if config.dedup_enabled:
            try:
                from trw_mcp.state.dedup import batch_dedup, is_migration_needed
                if is_migration_needed(trw_dir):
                    batch_dedup(trw_dir, reader, writer, config=config)
            except (ImportError, OSError, ValueError, TypeError):
                logger.debug("learning_migration_failed", exc_info=True)

        # Bayesian calibration of impact score (PRD-CORE-034)
        calibrated_impact = calibrate_impact(impact, config)

        # Fetch active learnings once — reused by soft-cap and distribution
        all_active: list[dict[str, object]] = []
        # Fail-open: listing failure must not block learning recording
        with contextlib.suppress(OSError, StateError, ValueError, TypeError):
            all_active = list_active_learnings(trw_dir)

        # Forced distribution soft-cap check (PRD-CORE-034-FR01)
        calibrated_impact, distribution_soft_cap_warning = check_soft_cap(
            calibrated_impact, all_active, config,
        )

        learning_id = generate_learning_id()

        # Semantic dedup check (PRD-CORE-042) — must run BEFORE storing
        safe_tags = tags or []
        safe_evidence = evidence or []
        dedup_result = check_and_handle_dedup(
            LearningParams(
                summary=summary,
                detail=detail,
                learning_id=learning_id,
                tags=safe_tags,
                evidence=safe_evidence,
                impact=calibrated_impact,
                shard_id=shard_id,
                source_type=source_type,
                source_identity=source_identity,
            ),
            entries_dir, reader, writer, config,
        )
        if dedup_result is not None:
            return dedup_result

        # Store via SQLite adapter (primary path) — after dedup to avoid orphans
        adapter_store(
            trw_dir,
            learning_id=learning_id,
            summary=summary,
            detail=detail,
            tags=safe_tags,
            evidence=safe_evidence,
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
                tags=safe_tags,
                evidence=safe_evidence,
                impact=calibrated_impact,
                shard_id=shard_id,
                source_type=source_type,
                source_identity=source_identity,
            )
            entry_path = save_learning_entry(trw_dir, entry)
            update_analytics(trw_dir, 1)
        except (OSError, ValueError, TypeError):
            entry_path = entries_dir / f"{learning_id}.yaml"

        # Forced distribution enforcement (PRD-CORE-034)
        distribution_warning, _demoted_ids = enforce_distribution(
            impact, calibrated_impact, learning_id,
            all_active, trw_dir, config,
        )

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
        config = get_config()
        writer = FileStateWriter()
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
                entries_dir = trw_dir / config.learnings_dir / config.entries_dir
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
                    writer.write_yaml(entry_path, data)
                    resync_learning_index(trw_dir)
            except (OSError, ValueError, TypeError):
                logger.debug("yaml_backup_update_failed", exc_info=True)

        return result

    @server.tool()
    @log_tool_call
    def trw_recall(
        query: str,
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = None,
        shard_id: str | None = None,
        max_results: int | None = None,
        compact: bool | None = None,
        topic: str | None = None,
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
            topic: Optional topic slug from knowledge topology. When provided,
                only returns learnings belonging to that topic cluster.
        """
        # Input validation (PRD-QUAL-042-FR06): impact bounds
        min_impact = max(0.0, min(1.0, min_impact))

        config = get_config()
        reader = FileStateReader()
        trw_dir = resolve_trw_dir()
        if max_results is None:
            max_results = config.recall_max_results
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()
        use_compact = compact if compact is not None else is_wildcard

        # Search entries via SQLite adapter (returns list of dicts directly)
        matching_learnings = adapter_recall(
            trw_dir, query=query, tags=tags, min_impact=min_impact,
            status=status, max_results=0, compact=False,  # get all, we rank locally
        )

        # Topic-scoped pre-filter (PRD-CORE-021-FR07)
        topic_filter_ignored = False
        if topic is not None:
            clusters_path = trw_dir / config.knowledge_output_dir / "clusters.json"
            try:
                if clusters_path.exists():
                    clusters_data = json.loads(clusters_path.read_text(encoding="utf-8"))
                    if topic in clusters_data:
                        allowed_ids = set(clusters_data[topic])
                        matching_learnings = [
                            e for e in matching_learnings
                            if str(e.get("id", "")) in allowed_ids
                        ]
                    else:
                        topic_filter_ignored = True
                else:
                    topic_filter_ignored = True
            except (json.JSONDecodeError, OSError):
                topic_filter_ignored = True

        # Update access tracking for recalled IDs
        matched_ids = [str(e.get("id", "")) for e in matching_learnings if e.get("id")]
        adapter_update_access(trw_dir, matched_ids)

        # Track each recalled learning for outcome-based calibration (PRD-CORE-034)
        try:
            from trw_mcp.state.recall_tracking import record_recall as _record_recall
            for lid in matched_ids:
                _record_recall(lid, query)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError):
            logger.debug("recall_tracking_failed", exc_info=True)

        # Augment local results with remote shared learnings (PRD-CORE-033)
        # NOTE: broad except kept intentionally — remote fetch is external code
        # that can raise arbitrary exceptions (network, auth, serialization).
        try:
            from trw_mcp.telemetry.remote_recall import fetch_shared_learnings
            remote = fetch_shared_learnings(query)
            if remote:
                matching_learnings = list(matching_learnings) + remote
        except Exception:  # noqa: BLE001 — fail-open for external service
            logger.debug("remote_recall_failed", exc_info=True)

        # Search patterns and rank all results by utility
        matching_patterns = search_patterns(
            trw_dir / config.patterns_dir, query_tokens, reader,
        )
        ranked_learnings = rank_by_utility(
            matching_learnings, query_tokens, config.recall_utility_lambda,
        )

        # Capture pre-cap counts for the total_available response field
        total_available = len(ranked_learnings) + len(matching_patterns)

        # Apply result cap
        if max_results > 0:
            ranked_learnings = ranked_learnings[:max_results]

        # Strip to compact fields when requested
        if use_compact:
            allowed = config.recall_compact_fields
            ranked_learnings = [
                {k: v for k, v in entry.items() if k in allowed}
                for entry in ranked_learnings
            ]

        # Skip context collection for compact wildcard queries (saves I/O)
        context_data: dict[str, object] = {}
        if not (is_wildcard and use_compact):
            context_data = collect_context(trw_dir, config.context_dir, reader)

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
            "topic_filter_ignored": topic_filter_ignored if topic is not None else False,
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
        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        llm = _create_llm_client()
        return execute_claude_md_sync(scope, target_dir, config, reader, writer, llm)
