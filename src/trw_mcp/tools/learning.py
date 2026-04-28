"""TRW self-learning tools — learn, recall, instructions_sync.

These 3 self-learning tools manage the .trw/ self-learning layer that makes
AI coding agents progressively more effective in a specific repository over time.
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

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    ClaudeMdSyncResultDict,
    LearnResultDict,
    RecallResultDict,
)
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import TRWCallContext, resolve_pin_key, resolve_trw_dir
from trw_mcp.state.analytics import (
    generate_learning_id,
    save_learning_entry,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.memory_adapter import (
    get_backend,
    list_active_learnings,
)
from trw_mcp.state.memory_adapter import (
    recall_learnings as adapter_recall,
)
from trw_mcp.state.memory_adapter import (
    store_learning as adapter_store,
)
from trw_mcp.state.memory_adapter import (
    update_access_tracking as adapter_update_access,
)
from trw_mcp.state.memory_adapter import (
    update_learning as adapter_update,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import (
    collect_context,
    search_patterns,
)
from trw_mcp.tools._learning_helpers import (
    check_and_handle_dedup,
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


def _build_call_ctx(ctx: Context | None) -> TRWCallContext:
    """PRD-CORE-141 FR03: build a TRWCallContext from an incoming FastMCP ctx.

    Used by ctx-aware learning tools so they don't scan-hijack another
    session's on-disk active run via telemetry or PRD knowledge-ID prefetch.
    """
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception:
        raw_session = None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def _read_injected_ids(trw_dir: Path) -> set[str]:
    """Read learning IDs already injected by the user-prompt-submit hook.

    PRD-CORE-095 FR15: Returns a set of IDs from
    ``.trw/context/injected_learning_ids.txt`` (one per line).
    Returns empty set if file missing or unreadable.
    """
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    try:
        return {line.strip() for line in state_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        pass
    return set()


def _annotate_injected_learnings(
    result: dict[str, object],
    trw_dir: Path,
) -> None:
    """Annotate and deprioritize already-injected learnings in recall results.

    PRD-CORE-095 FR15: Reads injected IDs from state file and moves
    already-injected learnings to the end of the list with an annotation.
    Fresh results fill the primary slots.
    """
    injected_ids = _read_injected_ids(trw_dir)
    if not injected_ids:
        return
    learnings = result.get("learnings")
    if not learnings or not isinstance(learnings, list):
        return
    fresh: list[dict[str, object]] = []
    already: list[dict[str, object]] = []
    for entry in learnings:
        lid = str(entry.get("id", ""))
        if lid in injected_ids:
            entry["already_in_context"] = True
            already.append(entry)
        else:
            fresh.append(entry)
    result["learnings"] = fresh + already


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

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_learn(
        ctx: Context | None = None,
        summary: str = "",
        detail: str = "",
        tags: list[str] | None = None,
        evidence: list[str] | None = None,
        impact: float = 0.5,
        shard_id: str | None = None,
        source_type: str = "agent",
        source_identity: str = "",
        client_profile: str | None = None,
        model_id: str | None = None,
        consolidated_from: list[str] | None = None,
        assertions: list[dict[str, str]] | None = None,
        # PRD-CORE-110: Typed learning fields
        type: str = "pattern",
        nudge_line: str = "",
        expires: str = "",
        confidence: str = "unverified",
        task_type: str = "",
        domain: list[str] | None = None,
        phase_origin: str = "",
        phase_affinity: list[str] | None = None,
        team_origin: str = "",
        protection_tier: str = "normal",
    ) -> LearnResultDict:
        """Persist a non-obvious discovery so future agents inherit the finding.

        Use when:
        - You just found a root cause, gotcha, or durable pattern worth remembering.
        - Capture at the moment you validated an approach that prevents repeated mistakes.
        - You hit an architecture constraint that is not obvious from reading the code.

        Only record learnings that:
        - prevent repeated mistakes;
        - change future implementation, debugging, or review behavior;
        - are specific enough to be useful when recalled later.

        Routine observations ("I read the file", "the test passed") are skipped —
        those degrade recall quality.

        Required:
        - summary: one-line headline (required).
        - detail: the full finding with context, symptoms, and why-it-matters (required).

        Recommended:
        - tags: keywords used by trw_recall filtering (recommended).
        - impact: 0.0-1.0; high values surface the entry more often.

        Advanced (auto-detected if omitted):
        - other fields (shard_id, source_*, client_profile, model_id,
          consolidated_from, assertions, type, nudge_line, expires, confidence,
          task_type, domain, phase_origin, phase_affinity, team_origin,
          protection_tier): auto-detected when omitted.
          Most learnings need only summary and detail. Adding tags and impact
          improves recall precision. All other fields are auto-detected.

        Output: LearnResultDict with fields
        {id: str, status: "saved"|"deduped"|"error", dedup_match?: dict, ceremony_hint?: str}.

        Example:
            trw_learn(summary="CI flake on macOS sqlite_vec load",
                      detail="macOS system Python lacks SQLITE_ENABLE_LOAD_EXTENSION; "
                             "trap AttributeError not sqlite3.Error",
                      tags=["macos","sqlite","flake"], impact=0.8)
            → {"id": "L-abc12345", "status": "saved"}

        See Also: trw_recall, trw_learn_update
        """
        # PRD-CORE-099: Auto-detect client and model when not explicitly provided.
        # None = "not provided" → auto-detect. Empty string = explicit blank.
        from trw_mcp.state.source_detection import detect_client_profile, detect_model_id
        from trw_mcp.tools._learn_impl import execute_learn

        if client_profile is None:
            client_profile = detect_client_profile()
        if model_id is None:
            model_id = detect_model_id()
        call_ctx = _build_call_ctx(ctx)

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
            client_profile=client_profile,
            model_id=model_id,
            consolidated_from=consolidated_from,
            assertions=assertions,
            is_solution_fn=_is_solution_summary,
            type=type,
            nudge_line=nudge_line,
            expires=expires,
            confidence=confidence,
            task_type=task_type,
            domain=domain,
            phase_origin=phase_origin,
            phase_affinity=phase_affinity,
            team_origin=team_origin,
            protection_tier=protection_tier,
            session_id=call_ctx.session_id or call_ctx.fastmcp_session,
            # Dependency injection: pass module-level refs for testability
            _adapter_store=adapter_store,
            _generate_learning_id=generate_learning_id,
            _save_learning_entry=save_learning_entry,
            _update_analytics=update_analytics,
            _list_active_learnings=list_active_learnings,
            _check_and_handle_dedup=check_and_handle_dedup,
        )

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_learn_update(
        ctx: Context | None = None,
        learning_id: str = "",
        status: str | None = None,
        detail: str | None = None,
        impact: float | None = None,
        summary: str | None = None,
        assertions: list[dict[str, str]] | None = None,
        # PRD-CORE-110: Typed learning update fields
        type: str | None = None,
        nudge_line: str | None = None,
        expires: str | None = None,
        confidence: str | None = None,
        task_type: str | None = None,
        domain: list[str] | None = None,
        phase_origin: str | None = None,
        phase_affinity: list[str] | None = None,
        team_origin: str | None = None,
        protection_tier: str | None = None,
        feedback: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, str]:
        """Update an existing learning — status, fields, or feedback signal.

        Use when:
        - The issue a learning describes has been fixed (status="resolved").
        - A pattern is no longer applicable (status="obsolete").
        - Detail or summary can be sharpened now that root cause is clearer.
        - You want to boost/demote an entry's recall ranking via feedback.

        Output: dict with fields {status: "updated"|"not_found"|"invalid", error?: str,
        field_updated?: str}.

        Args:
            learning_id: ID of the learning to update (e.g., "L-abc12345").
            status: New status — "active", "resolved", or "obsolete". Resolved/obsolete entries stop appearing in recall.
            detail: Updated detail text (replaces existing detail).
            impact: Updated impact score (0.0-1.0).
            summary: Updated summary text (replaces existing summary).
            assertions: Replace assertions on this entry (PRD-CORE-086 FR12). Empty list removes all.
            type: Updated type — "incident", "pattern", "convention", "hypothesis", or "workaround".
            nudge_line: Updated nudge text (max 80 chars, auto-truncated).
            expires: Updated expiration date/condition.
            confidence: Updated confidence — "unverified", "low", "medium", "high", or "verified".
            task_type: Updated task type identifier.
            domain: Updated domain tags.
            phase_affinity: Updated phase affinities.
            protection_tier: Updated protection tier.
            feedback: Signal whether this learning was helpful or unhelpful — "helpful" or "unhelpful". Affects recall ranking via feedback-aware decay (PRD-CORE-132).
            tags: Replace the entry's tag set. Passing `[]` clears all tags. Callers are responsible for dedup/normalization.
        """
        config = get_config()
        writer = FileStateWriter()
        trw_dir = resolve_trw_dir()

        # PRD-CORE-110: Validate enum fields before forwarding to adapter
        _valid_types = {"incident", "pattern", "convention", "hypothesis", "workaround"}
        if type is not None and type not in _valid_types:
            return {"error": f"Invalid type '{type}'. Must be one of: {_valid_types}", "status": "invalid"}
        _valid_confidences = {"unverified", "low", "medium", "high", "verified"}
        if confidence is not None and confidence not in _valid_confidences:
            return {
                "error": f"Invalid confidence '{confidence}'. Must be one of: {_valid_confidences}",
                "status": "invalid",
            }
        _valid_tiers = {"critical", "high", "normal", "low", "protected", "permanent"}
        if protection_tier is not None and protection_tier not in _valid_tiers:
            return {
                "error": f"Invalid protection_tier '{protection_tier}'. Must be one of: {_valid_tiers}",
                "status": "invalid",
            }
        _valid_phases = {"", "RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"}
        if phase_origin is not None and phase_origin not in _valid_phases:
            return {
                "error": f"Invalid phase_origin '{phase_origin}'. Must be one of: {_valid_phases}",
                "status": "invalid",
            }
        if nudge_line is not None and len(nudge_line) > 80:
            return {"error": f"nudge_line exceeds 80 chars ({len(nudge_line)})", "status": "invalid"}
        _valid_feedback = {"helpful", "unhelpful"}
        if feedback is not None and feedback not in _valid_feedback:
            return {"error": f"Invalid feedback '{feedback}'. Must be one of: {_valid_feedback}", "status": "invalid"}
        if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)):
            return {"error": "tags must be a list of strings", "status": "invalid"}

        # PRD-CORE-132 FR03: Increment feedback counter in backend
        if feedback is not None:
            try:
                backend = get_backend(trw_dir)
                existing = backend.get(learning_id)
                if existing is not None:
                    if feedback == "helpful":
                        backend.update(learning_id, helpful_count=existing.helpful_count + 1)
                    else:
                        backend.update(learning_id, unhelpful_count=existing.unhelpful_count + 1)
            except Exception:  # justified: fail-open, feedback must not block learn_update
                logger.debug("feedback_update_failed", learning_id=learning_id, feedback=feedback, exc_info=True)

        # Validate and store assertions via backend (PRD-CORE-086 FR12)
        validated_assertions: list[dict[str, object]] | None = None
        if assertions is not None:
            from trw_memory.models.memory import Assertion

            validated: list[Assertion] = [Assertion.model_validate(a, strict=False) for a in assertions]
            validated_assertions = [a.model_dump() for a in validated]
            try:
                backend = get_backend(trw_dir)
                existing = backend.get(learning_id)
                if existing is not None:
                    existing.assertions = validated
                    backend.update(learning_id, assertions=validated_assertions)
            except Exception:  # justified: fail-open, assertion persistence must not block learn_update
                logger.debug("assertion_update_failed", learning_id=learning_id, exc_info=True)

        result = adapter_update(
            trw_dir,
            learning_id=learning_id,
            status=status,
            detail=detail,
            impact=impact,
            summary=summary,
            type=type,
            nudge_line=nudge_line,
            expires=expires,
            confidence=confidence,
            task_type=task_type,
            domain=domain,
            phase_origin=phase_origin,
            phase_affinity=phase_affinity,
            team_origin=team_origin,
            protection_tier=protection_tier,
            tags=tags,
        )

        # Dual-write: also update YAML backup for rollback safety
        if result.get("status") == "updated":
            _updated_field = (
                "status"
                if status is not None
                else "detail"
                if detail is not None
                else "summary"
                if summary is not None
                else "impact"
                if impact is not None
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
                    if validated_assertions is not None:
                        data["assertions"] = validated_assertions
                    data["updated"] = _today_iso
                    # PRD-CORE-110: Sync typed fields to YAML backup
                    if type is not None:
                        data["type"] = type
                    if nudge_line is not None:
                        data["nudge_line"] = nudge_line
                    if expires is not None:
                        data["expires"] = expires
                    if confidence is not None:
                        data["confidence"] = confidence
                    if task_type is not None:
                        data["task_type"] = task_type
                    if domain is not None:
                        data["domain"] = domain
                    if phase_origin is not None:
                        data["phase_origin"] = phase_origin
                    if phase_affinity is not None:
                        data["phase_affinity"] = phase_affinity
                    if team_origin is not None:
                        data["team_origin"] = team_origin
                    if protection_tier is not None:
                        data["protection_tier"] = protection_tier
                    if tags is not None:
                        data["tags"] = tags
                    writer.write_yaml(entry_path, data)
                    resync_learning_index(trw_dir)
            except (OSError, ValueError, TypeError):
                logger.debug("yaml_backup_update_failed", exc_info=True)

        return result

    @server.tool()
    @log_tool_call
    def trw_recall(
        ctx: Context | None = None,
        query: str = "",
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = "active",
        shard_id: str | None = None,
        max_results: int | None = None,
        compact: bool | None = None,
        ultra_compact: bool = False,
        topic: str | None = None,
    ) -> RecallResultDict:
        """Retrieve prior learnings relevant to your current task.

        Use when:
        - You are about to work in an unfamiliar area of the codebase.
        - You suspect a bug has been seen before and want prior root-cause notes.
        - You want a narrow tag/impact slice before spawning a subagent.

        See Also: trw_learn, trw_session_start.

        Results are ranked by combined relevance (query match on summary/tags/detail)
        and utility (impact, type-aware recency decay, prior feedback). Context
        boosts prioritize entries matching your current domain, phase, and team.

        Output: RecallResultDict with fields
        {learnings: list[{id, summary, detail?, tags, impact, ...}],
         count: int, query: str, ceremony_hint?: str}.

        Example:
            trw_recall(query="sqlite extension load mac", min_impact=0.6)
            → {"learnings": [{"id": "L-abc12345", "summary": "...", ...}], "count": 3}

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
            ultra_compact: When True, return only ``{learnings, count, ceremony_hint}``
                with each learning reduced to ``{id, summary}``.
            topic: Optional topic slug from knowledge topology. When provided,
                only returns learnings belonging to that topic cluster.

        See Also: trw_learn
        """
        from trw_mcp.tools._recall_impl import execute_recall

        # PRD-CORE-141 FR03: build call_ctx so downstream find_active_run()
        # inside build_recall_context doesn't scan-hijack another session.
        call_ctx = _build_call_ctx(ctx)
        trw_dir = resolve_trw_dir()
        injected_ids = _read_injected_ids(trw_dir)
        # Resolve from this module's namespace so test patches work
        result = execute_recall(
            query=query,
            trw_dir=trw_dir,
            config=get_config(),
            tags=tags,
            min_impact=min_impact,
            status=status,
            shard_id=shard_id,
            max_results=max_results,
            deprioritized_ids=injected_ids,
            compact=compact,
            ultra_compact=ultra_compact,
            topic=topic,
            call_ctx=call_ctx,
            # Dependency injection: pass module-level refs for testability
            _adapter_recall=adapter_recall,
            _adapter_update_access=adapter_update_access,
            _search_patterns=search_patterns,
            _rank_by_utility=rank_by_utility,
            _collect_context=collect_context,
        )

        # PRD-CORE-095 FR15: Annotate already-injected learnings
        if not ultra_compact:
            _annotate_injected_learnings(
                result,  # type: ignore[arg-type]  # RecallResultDict is a dict subclass
                trw_dir,
            )

        return result

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_instructions_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
    ) -> ClaudeMdSyncResultDict:
        """Sync TRW protocol and ceremony guidance into the client's instruction file.

        Use when:
        - Onboarding a new project and the instruction file (CLAUDE.md / AGENTS.md)
          does not yet contain the TRW auto-generated section.
        - You've changed the behavioral protocol template and need it re-rendered.
        - You switch IDE clients and need the correct surface written.

        Renders behavioral protocol and ceremony guidance into the auto-generated
        block of whichever client surface is present (``CLAUDE.md``, ``AGENTS.md``,
        ``.codex/INSTRUCTIONS.md``). Learnings are not promoted into the instruction file —
        trw_session_start() recall handles that (PRD-CORE-093).

        Output: ClaudeMdSyncResultDict with fields
        {status: "success"|"error", files_written: list[str], sections_synced: int}.

        Example:
            trw_instructions_sync(client="auto")
            → {"status": "success", "files_written": ["CLAUDE.md"], "sections_synced": 1}

        Args:
            scope: Sync scope — "root" for project instruction file, "sub" for module-level.
            target_dir: Target directory for sub-instruction file generation.
            client: Target client(s) to write instructions for.
                "auto" (default) — detect via IDE config dirs;
                "claude-code" — write CLAUDE.md only;
                "opencode" — write AGENTS.md only;
                "codex" — write .codex/INSTRUCTIONS.md only;
                "all" — write every detected/known client surface.
        """
        config = get_config()
        reader = FileStateReader()
        llm = _create_llm_client()
        return execute_claude_md_sync(scope, target_dir, config, reader, llm, client)

    @server.tool(name="trw_claude_md_sync", output_schema=None)
    @log_tool_call
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
    ) -> ClaudeMdSyncResultDict:
        """Deprecated alias for ``trw_instructions_sync``.

        Use when: maintaining backward compatibility with older callers; prefer
        ``trw_instructions_sync`` in new code. This alias emits a deprecation
        warning on every invocation and will be removed in a future release.

        Output: same as trw_instructions_sync — ClaudeMdSyncResultDict with fields
        {status, files_written, sections_synced}.
        """
        logger.warning(
            "deprecated_tool_alias_used",
            tool="trw_claude_md_sync",
            canonical="trw_instructions_sync",
            note="trw_claude_md_sync is deprecated; use trw_instructions_sync. Alias will be removed in a future release.",
        )
        config = get_config()
        reader = FileStateReader()
        llm = _create_llm_client()
        return execute_claude_md_sync(scope, target_dir, config, reader, llm, client)
