"""TRW self-learning tools — reflect, learn, learn_update, recall, script_save, claude_md_sync, learn_prune.

These 7 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
When the optional ``claude-agent-sdk`` package is installed, several tools
gain LLM-augmented behavior (better summaries, relevance classification).

Decomposed per PRD-FIX-010: tool stubs delegate to focused state modules.
"""

from __future__ import annotations

from datetime import date

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import detect_current_phase, resolve_trw_dir
from trw_mcp.state.analytics import (
    compute_reflection_quality,
    find_entry_by_id,
    generate_learning_id,
    resync_learning_index,
    save_learning_entry,
    update_analytics,
    update_analytics_extended,
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

logger = structlog.get_logger()


_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_llm = LLMClient(model=_config.llm_default_model)


def register_learning_tools(server: FastMCP) -> None:
    """Register all self-learning tools on the MCP server."""

    @server.tool()
    def trw_reflect(
        run_path: str | None = None,
        scope: str = "session",
    ) -> dict[str, object]:
        """Analyze recent work events and extract structured learnings for .trw/.

        Args:
            run_path: Path to run directory for run-scoped reflection.
            scope: Reflection scope — "session", "run", or "wave".
        """
        from trw_mcp.state.reflection import (
            collect_reflection_inputs,
            create_reflection_record,
            generate_reflection_learnings,
            persist_reflection,
        )

        trw_dir = resolve_trw_dir()
        _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)
        _writer.ensure_dir(trw_dir / _config.reflections_dir)

        inputs = collect_reflection_inputs(run_path, trw_dir)
        new_learnings, llm_used, positive_count = generate_reflection_learnings(inputs, trw_dir)
        reflection = create_reflection_record(inputs, new_learnings, scope)
        persist_reflection(trw_dir, reflection, run_path, scope, len(new_learnings))

        update_analytics_extended(
            trw_dir, len(new_learnings),
            is_reflection=True,
            is_success=len(inputs.error_events) == 0 and len(inputs.events) > 0,
        )
        reflection_quality = compute_reflection_quality(trw_dir)

        logger.info(
            "trw_reflect_complete",
            scope=scope,
            events_analyzed=len(inputs.events),
            learnings_produced=len(new_learnings),
            reflection_quality=reflection_quality.get("score", 0.0),
        )

        return {
            "reflection_id": reflection.id,
            "scope": scope,
            "events_analyzed": len(inputs.events),
            "new_learnings": new_learnings,
            "error_patterns": len(inputs.error_events),
            "repeated_operations": len(inputs.repeated_ops),
            "success_patterns": {
                "count": len(inputs.success_patterns),
                "phase_completions": [
                    {"phase": str(e.get("event")), "events_in_phase": 1}
                    for e in inputs.phase_transitions
                ],
                "shard_successes": [
                    {
                        "event_type": sp["event_type"],
                        "count": int(sp.get("count", 1)),
                        "first_attempt": True,
                    }
                    for sp in inputs.success_patterns
                ],
                "tool_sequences": inputs.tool_sequences,
            },
            "validated_learnings": inputs.validated_learnings,
            "positive_learnings_created": positive_count,
            "llm_used": llm_used,
            "reflection_quality": reflection_quality,
        }

    @server.tool()
    def trw_learn(
        summary: str,
        detail: str,
        tags: list[str] | None = None,
        evidence: list[str] | None = None,
        impact: float = 0.5,
        shard_id: str | None = None,
        source_type: str = "agent",
        source_identity: str = "",
    ) -> dict[str, str]:
        """Record a specific learning entry manually to .trw/learnings/.

        Args:
            summary: One-line summary of the learning.
            detail: Detailed description with context.
            tags: Categorization tags (e.g., ["testing", "gotcha"]).
            evidence: Supporting evidence (file paths, error messages, etc.).
            impact: Impact score from 0.0 to 1.0 (higher = more important).
            shard_id: Optional shard identifier for sub-agent attribution.
            source_type: Learning provenance — "human" or "agent".
            source_identity: Name of source (e.g., "Tyler", "claude-opus-4-6").
        """
        trw_dir = resolve_trw_dir()
        _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)

        learning_id = generate_learning_id()
        current_phase = detect_current_phase()
        entry = LearningEntry(
            id=learning_id, summary=summary, detail=detail,
            tags=tags or [], evidence=evidence or [],
            impact=impact, shard_id=shard_id,
            phase_scope=current_phase,
            source_type=source_type,
            source_identity=source_identity,
        )
        entry_path = save_learning_entry(trw_dir, entry)
        update_analytics(trw_dir, 1)

        logger.info("trw_learn_recorded", learning_id=learning_id, summary=summary, impact=impact)
        return {"learning_id": learning_id, "path": str(entry_path), "status": "recorded"}

    @server.tool()
    def trw_learn_update(
        learning_id: str,
        status: str | None = None,
        impact: float | None = None,
        summary: str | None = None,
        detail: str | None = None,
        tags: list[str] | None = None,
        source_type: str | None = None,
        source_identity: str | None = None,
    ) -> dict[str, str]:
        """Update an existing learning entry in .trw/learnings/.

        Args:
            learning_id: ID of the learning entry to update (e.g. 'L-abcd1234').
            status: New status — 'active', 'resolved', or 'obsolete'.
            impact: New impact score (0.0-1.0).
            summary: Updated one-line summary.
            detail: Updated detailed description.
            tags: Replacement tag list.
            source_type: Learning provenance — 'human' or 'agent'.
            source_identity: Name of source (e.g., 'Tyler', 'claude-opus-4-6').
        """
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

        if not entries_dir.exists():
            return {"learning_id": learning_id, "error": "No entries directory found"}

        valid_statuses = {s.value for s in LearningStatus}
        if status is not None and status not in valid_statuses:
            return {
                "learning_id": learning_id,
                "error": f"Invalid status: {status!r}. Valid: {sorted(valid_statuses)}",
            }

        valid_source_types = {"human", "agent"}
        if source_type is not None and source_type not in valid_source_types:
            return {
                "learning_id": learning_id,
                "error": f"Invalid source_type: {source_type!r}. Valid: {sorted(valid_source_types)}",
            }

        found = find_entry_by_id(entries_dir, learning_id)
        if found is None:
            return {"learning_id": learning_id, "error": "Learning entry not found"}
        target_path, target_data = found

        if status is not None:
            target_data["status"] = status
            if status == LearningStatus.RESOLVED.value:
                target_data["resolved_at"] = date.today().isoformat()
        if impact is not None:
            target_data["impact"] = impact
        if summary is not None:
            target_data["summary"] = summary
        if detail is not None:
            target_data["detail"] = detail
        if tags is not None:
            target_data["tags"] = tags
        if source_type is not None:
            target_data["source_type"] = source_type
        if source_identity is not None:
            target_data["source_identity"] = source_identity

        target_data["updated"] = date.today().isoformat()
        _writer.write_yaml(target_path, target_data)
        resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_updated",
            learning_id=learning_id,
            fields_changed=[
                k for k, v in [
                    ("status", status), ("impact", impact),
                    ("summary", summary), ("detail", detail), ("tags", tags),
                    ("source_type", source_type), ("source_identity", source_identity),
                ] if v is not None
            ],
        )
        return {"learning_id": learning_id, "path": str(target_path), "status": "updated"}

    @server.tool()
    def trw_recall(
        query: str,
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = None,
        shard_id: str | None = None,
        max_results: int = _config.recall_max_results,
        compact: bool | None = None,
    ) -> dict[str, object]:
        """Search learnings and patterns relevant to a query from .trw/.

        Args:
            query: Search query (keywords matched against summaries/details).
                Use "*" to list all (auto-enables compact mode).
            tags: Optional tag filter — only return entries matching these tags.
            min_impact: Minimum impact score filter (0.0-1.0).
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

        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        matching_learnings, matched_files = search_entries(
            entries_dir, query_tokens, _reader,
            tags=tags, min_impact=min_impact, status=status,
        )
        matched_ids = update_access_tracking(matched_files, _reader, _writer)
        log_recall_receipt(trw_dir, query, matched_ids, shard_id=shard_id)

        matching_patterns = search_patterns(
            trw_dir / _config.patterns_dir, query_tokens, _reader,
        )
        current_phase = detect_current_phase()
        ranked_learnings = rank_by_utility(
            matching_learnings, query_tokens, _config.recall_utility_lambda,
            current_phase=current_phase,
        )

        total_learnings_available = len(ranked_learnings)
        total_patterns_available = len(matching_patterns)

        if max_results > 0:
            ranked_learnings = ranked_learnings[:max_results]

        if use_compact:
            compact_fields = _config.recall_compact_fields
            ranked_learnings = [
                {k: v for k, v in entry.items() if k in compact_fields}
                for entry in ranked_learnings
            ]

        context_data: dict[str, object] = {}
        if not (is_wildcard and use_compact):
            context_data = collect_context(trw_dir, _config.context_dir, _reader)

        logger.info(
            "trw_recall_searched", query=query,
            learnings_found=len(ranked_learnings),
            patterns_found=len(matching_patterns), compact=use_compact,
        )

        return {
            "query": query,
            "learnings": ranked_learnings,
            "patterns": matching_patterns,
            "context": context_data,
            "total_matches": len(ranked_learnings) + len(matching_patterns),
            "total_available": total_learnings_available + total_patterns_available,
            "compact": use_compact,
            "max_results": max_results,
        }

    @server.tool()
    def trw_script_save(
        name: str, content: str, description: str, language: str = "bash",
    ) -> dict[str, str]:
        """Save a reusable script to .trw/scripts/ for cross-session reuse.

        Args:
            name: Script name (used as filename stem, alphanumeric + hyphens).
            content: Script content.
            description: What the script does.
            language: Script language — "bash", "python", etc.
        """
        from trw_mcp.state.scripts import save_script

        trw_dir = resolve_trw_dir()
        script_path, action = save_script(trw_dir, name, content, description, language)

        logger.info("trw_script_saved", name=name, action=action, path=str(script_path))
        return {"name": name, "path": str(script_path), "status": action}

    @server.tool()
    def trw_claude_md_sync(
        scope: str = "root", target_dir: str | None = None,
    ) -> dict[str, object]:
        """Generate/update CLAUDE.md from high-impact .trw/ learnings.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
        """
        return execute_claude_md_sync(scope, target_dir, _config, _reader, _writer, _llm)

    @server.tool()
    def trw_learn_prune(dry_run: bool = True) -> dict[str, object]:
        """Review active learnings and mark resolved/obsolete ones.

        Args:
            dry_run: If True (default), report candidates without applying changes.
        """
        from trw_mcp.state.pruning import execute_prune

        trw_dir = resolve_trw_dir()
        result = execute_prune(trw_dir, dry_run=dry_run)

        candidates = result.get("candidates", [])
        duplicates = result.get("duplicates", [])
        logger.info(
            "trw_learn_prune_complete",
            dry_run=dry_run,
            candidates=len(candidates) if isinstance(candidates, list) else 0,
            actions=result.get("actions", 0),
            receipts_pruned=result.get("receipts_pruned", 0),
            method=result.get("method", "none"),
            duplicates_found=len(duplicates) if isinstance(duplicates, list) else 0,
        )
        return result
