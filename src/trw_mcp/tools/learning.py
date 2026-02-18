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
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.analytics import (
    generate_learning_id,
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

logger = structlog.get_logger()


_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()
_llm = LLMClient(model=_config.llm_default_model)


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
        trw_dir, _ = _entries_path()

        learning_id = generate_learning_id()
        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags or [],
            evidence=evidence or [],
            impact=impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
        )
        entry_path = save_learning_entry(trw_dir, entry)
        update_analytics(trw_dir, 1)

        logger.info("trw_learn_recorded", learning_id=learning_id, summary=summary, impact=impact)
        return {"learning_id": learning_id, "path": str(entry_path), "status": "recorded"}

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
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
    ) -> dict[str, object]:
        """Generate/update CLAUDE.md from high-impact .trw/ learnings.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
        """
        return execute_claude_md_sync(scope, target_dir, _config, _reader, _writer, _llm)
