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
# ruff: noqa: I001 - facade imports stay grouped for monkeypatch seams and LOC ratchet.

from __future__ import annotations

import structlog
from fastmcp import Context, FastMCP

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
    check_and_handle_dedup,
)
from trw_mcp.tools._learning_module_helpers import _annotate_injected_learnings, _build_call_ctx, _coerce_tags
from trw_mcp.tools._learning_module_helpers import _coerce_learn_type, _is_solution_summary, _validate_learn_enums
from trw_mcp.tools._learning_module_helpers import _create_llm_client, _read_injected_ids
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def register_learning_tools(server: FastMCP) -> None:
    """Register self-learning tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_learn(
        ctx: Context | None = None,
        summary: str = "",
        detail: str = "",
        tags: list[str] | str | None = None,
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
        # PRD-CORE-185 FR07: write-tier override.
        scope: str = "auto",
    ) -> LearnResultDict:
        """Persist a non-obvious discovery so future agents inherit the finding.

        Use when:
        - You just found a root cause, gotcha, or durable pattern worth remembering.
        - Capture it the moment you validate an approach that prevents repeated mistakes.
        - You hit an architecture constraint that is not obvious from reading the code.

        Only record learnings that:
        - prevent repeated mistakes,
        - change future implementation/debugging/review behavior,
        - are specific enough to recall later.
        Routine observations ("I read the file", "the test passed") degrade
        recall quality.

        Required:
        - summary: one-line headline.
        - detail: full finding with context, symptoms, and why it matters.

        Recommended:
        - tags: keywords for trw_recall filtering. Accepts a JSON list
          (``["a","b"]``) OR a comma/whitespace-separated string (``"a,b c"``).
        - impact: 0.0-1.0; high values surface more often.

        Advanced (auto-detected if omitted):
        - shard/source/client/model/type/domain/phase/team/protection metadata.
        - scope: write-tier override (PRD-CORE-185). "auto" (default) routes
          portable learnings to the machine-local user tier when a user-scope
          store is present, else the project tier; "project"/"user" force it.
        Most learnings need only summary and detail. Adding tags and impact
        improves recall precision. All other fields are auto-detected.

        Output: LearnResultDict with
        {id: str, status: "saved"|"deduped"|"error", dedup_match?: dict, ceremony_hint?: str}.

        See Also: trw_recall, trw_learn_update
        """
        # PRD-CORE-099: Auto-detect client and model when not explicitly provided.
        # None = "not provided" → auto-detect. Empty string = explicit blank.
        from trw_mcp.state.source_detection import detect_client_profile, detect_model_id
        from trw_mcp.tools._learn_impl import execute_learn

        # Potemkin defect C: coerce advertised type aliases (e.g. 'gotcha',
        # presented as first-class in the docstring + trw-deliver skill) to a
        # valid MemoryType BEFORE enum validation — genuine nonsense still
        # falls through to an honest rejection below.
        type = _coerce_learn_type(type)

        # core185-ENUM-UNGUARDED-3: validate enum args BEFORE forwarding so an
        # invalid value returns a structured rejection rather than an unhandled
        # ValueError from the downstream enum construction (mirrors trw_learn_update).
        _enum_reject = _validate_learn_enums(type=type, confidence=confidence, protection_tier=protection_tier)
        if _enum_reject is not None:
            return _enum_reject

        if client_profile is None:
            client_profile = detect_client_profile()
        if model_id is None:
            model_id = detect_model_id()
        call_ctx = _build_call_ctx(ctx)

        # PRD-IMPROVE-MCP-01 FR1: accept a comma/whitespace-separated string for
        # tags, not just a JSON list, before forwarding to the impl.
        coerced_tags = _coerce_tags(tags)

        # Resolve from this module's namespace so test patches work
        return execute_learn(
            summary=summary,
            detail=detail,
            trw_dir=resolve_trw_dir(),
            config=get_config(),
            tags=coerced_tags,
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
            scope=scope,
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
        supersedes: str | None = None,
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
            supersedes: id of a PRIOR learning that THIS learning replaces/corrects (PRD-CORE-194 FR04). Closes the prior record's validity window (sets its invalid_from + invalidated_by=this id) and RETAINS it — never a delete. Fires ONLY when explicitly passed; a routine field edit never closes a window.
        """
        config = get_config()
        writer = FileStateWriter()
        trw_dir = resolve_trw_dir()

        # PRD-CORE-110: Validate enum fields before forwarding to adapter.
        # Potemkin defect C: coerce advertised type aliases (e.g. 'gotcha')
        # the same way trw_learn does, so the two tools share a type vocabulary.
        if type is not None:
            type = _coerce_learn_type(type)
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
            supersedes=supersedes,
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
        token_budget: int | None = None,
        # PRD-CORE-185 FR07: tier-scoping.
        include_tiers: list[str] | None = None,
        # PRD-CORE-194 FR03: bi-temporal validity time-travel.
        as_of: str | None = None,
        include_superseded: bool = False,
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
            token_budget: Optional max token ceiling for the serialized result.
                Must be > 0. When omitted, a sane default cap is applied so a
                recall can never overflow the context window (anti-collapse guard).
            include_tiers: Optional tier scope (PRD-CORE-185). Project entries
                are ALWAYS included; this flag only controls whether machine-local
                USER-tier entries are added on top. None (default) and any list
                containing "user" federate the user tier when a user-scope store
                is present; ["project"] (no "user") restricts to project-only.
                A user-only query is intentionally not expressible -- the project
                tier is the local source of truth and is never excluded.
            as_of: Optional ISO-8601 instant (PRD-CORE-194). Time-travel recall —
                returns records whose validity window contained T. Malformed values
                raise a clean validation error. Default None = open records only.
            include_superseded: When True, also return superseded records, ranked
                strictly below open ones (each flagged superseded/invalidated_by).

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
            token_budget=token_budget,
            deprioritized_ids=injected_ids,
            compact=compact,
            ultra_compact=ultra_compact,
            topic=topic,
            call_ctx=call_ctx,
            include_tiers=include_tiers,
            as_of=as_of,
            include_superseded=include_superseded,
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
