"""TRW self-learning tools — learn, recall, claude_md_sync.

These 3 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
The ``anthropic`` SDK (optional [ai] dependency) provides LLM-augmented
behavior for several tools (better summaries, relevance classification).

Heavy business logic is delegated to ``_learn_impl.execute_learn`` and
``_recall_impl.execute_recall``; this module retains the FastMCP registration
closures, backward-compat shim, and module-level imports that test suites
patch at ``trw_mcp.tools.learning.*``.
"""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    ClaudeMdSyncResultDict,
    LearnResultDict,
    RecallResultDict,
)
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.analytics import (
    generate_learning_id,
    save_learning_entry,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.memory_adapter import (
    get_backend,
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

logger = structlog.get_logger(__name__)

# PRD-FIX-052-FR05: Solution-indicator patterns for auto-'pattern' tag suggestion
_SOLUTION_PATTERNS = _re.compile(
    r"(?:use .+ instead|prefer |always |best practice|"
    r"recommended approach|the fix is|pattern:)",
    flags=_re.IGNORECASE | _re.VERBOSE,
)


def _is_solution_summary(summary: str) -> bool:
    """Return True if the summary matches solution-indicator patterns (FR05)."""
    return bool(_SOLUTION_PATTERNS.search(summary))


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
        consolidated_from: list[str] | None = None,
        assertions: list[dict[str, str]] | None = None,
    ) -> LearnResultDict:
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
            consolidated_from: IDs of superseded entries to auto-mark as obsolete (PRD-FIX-052-FR04).
            assertions: Machine-verifiable assertions (PRD-CORE-086). Each dict has type, pattern, target.

        See Also: trw_recall, trw_learn_update
        """
        from trw_mcp.tools._learn_impl import execute_learn

        # Resolve from this module's namespace so test patches work
        return execute_learn(
            summary=summary,
            detail=detail,
            trw_dir=resolve_trw_dir(),
            config=get_config(),
            tags=tags,
            evidence=evidence,
            impact=impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
            consolidated_from=consolidated_from,
            assertions=assertions,
            is_solution_fn=_is_solution_summary,
            # Dependency injection: pass module-level refs for testability
            _adapter_store=adapter_store,
            _generate_learning_id=generate_learning_id,
            _save_learning_entry=save_learning_entry,
            _update_analytics=update_analytics,
            _list_active_learnings=list_active_learnings,
            _check_and_handle_dedup=check_and_handle_dedup,
        )

    @server.tool()
    @log_tool_call
    def trw_learn_update(
        learning_id: str,
        status: str | None = None,
        detail: str | None = None,
        impact: float | None = None,
        summary: str | None = None,
        assertions: list[dict[str, str]] | None = None,
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
            assertions: Replace assertions on this entry (PRD-CORE-086 FR12). Empty list removes all.
        """
        config = get_config()
        writer = FileStateWriter()
        trw_dir = resolve_trw_dir()

        # Validate and store assertions via backend (PRD-CORE-086 FR12)
        if assertions is not None:
            from trw_memory.models.memory import Assertion

            validated: list[Assertion] = [Assertion.model_validate(a) for a in assertions]
            try:
                backend = get_backend(trw_dir)
                existing = backend.get(learning_id)
                if existing is not None:
                    existing.assertions = validated
                    backend.update(learning_id, assertions=[a.model_dump() for a in validated])
            except Exception:  # justified: fail-open, assertion persistence must not block learn_update
                logger.debug("assertion_update_failed", learning_id=learning_id, exc_info=True)

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
            _updated_field = (
                "status" if status is not None
                else "detail" if detail is not None
                else "summary" if summary is not None
                else "impact" if impact is not None
                else "unknown"
            )
            logger.info("learn_update_ok", id=learning_id, field_updated=_updated_field)
            try:
                from datetime import datetime, timezone

                from trw_mcp.state.analytics import find_entry_by_id, resync_learning_index

                entries_dir = trw_dir / config.learnings_dir / config.entries_dir
                found = find_entry_by_id(entries_dir, learning_id)
                if found is not None:
                    entry_path, data = found
                    _today_iso = datetime.now(tz=timezone.utc).date().isoformat()
                    if status is not None:
                        data["status"] = status
                        if status in ("resolved", "obsolete"):
                            data["resolved_at"] = _today_iso
                    if detail is not None:
                        data["detail"] = detail
                    if summary is not None:
                        data["summary"] = summary
                    if impact is not None:
                        data["impact"] = impact
                    data["updated"] = _today_iso
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
    ) -> RecallResultDict:
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

        See Also: trw_learn, trw_knowledge_sync
        """
        from trw_mcp.tools._recall_impl import execute_recall

        # Resolve from this module's namespace so test patches work
        return execute_recall(
            query=query,
            trw_dir=resolve_trw_dir(),
            config=get_config(),
            tags=tags,
            min_impact=min_impact,
            status=status,
            shard_id=shard_id,
            max_results=max_results,
            compact=compact,
            topic=topic,
            # Dependency injection: pass module-level refs for testability
            _adapter_recall=adapter_recall,
            _adapter_update_access=adapter_update_access,
            _search_patterns=search_patterns,
            _rank_by_utility=rank_by_utility,
            _collect_context=collect_context,
        )

    @server.tool()
    @log_tool_call
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
    ) -> ClaudeMdSyncResultDict:
        """Promote your best learnings into CLAUDE.md — the next session starts with your insights built in.

        Renders high-impact learnings, behavioral protocol, ceremony guidance, and
        patterns into the auto-generated CLAUDE.md section. This is how individual
        session discoveries become permanent project instructions.

        Also writes AGENTS.md for opencode users (FR13) when detected or explicitly
        requested via the ``client`` parameter.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
            client: Target client(s) to write instructions for.
                "auto" (default) — detect via IDE config dirs (.claude/, .opencode/);
                "claude-code" — write CLAUDE.md only;
                "opencode" — write AGENTS.md only;
                "all" — write both CLAUDE.md and AGENTS.md.
        """
        config = get_config()
        reader = FileStateReader()
        llm = _create_llm_client()
        return cast(
            "ClaudeMdSyncResultDict", execute_claude_md_sync(scope, target_dir, config, reader, llm, client)
        )
