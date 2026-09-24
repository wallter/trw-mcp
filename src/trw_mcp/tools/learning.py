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
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.analytics import (
    generate_learning_id,
    save_learning_entry,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync, instruction_write_trigger
from trw_mcp.state.memory_adapter import (
    list_active_learnings,
    recall_learnings as adapter_recall,
    store_learning as adapter_store,
    update_learning as adapter_update,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import (
    check_and_handle_dedup,
)
from trw_mcp.tools._learn_arg_bags import parse_learn_metadata, parse_learn_update_fields
from trw_mcp.tools._learning_module_helpers import _build_call_ctx, _coerce_tags
from trw_mcp.tools._learning_module_helpers import _coerce_learn_type, _is_solution_summary, _validate_learn_enums
from trw_mcp.tools._learn_update_impl import execute_learn_update
from trw_mcp.tools._learning_module_helpers import _create_llm_client, _read_injected_ids

logger = structlog.get_logger(__name__)


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def register_learning_tools(server: FastMCP) -> None:
    """Register self-learning tools on the MCP server."""

    @server.tool(output_schema=None)
    def trw_learn(
        ctx: Context | None = None,
        summary: str | None = None,
        detail: str | None = None,
        tags: list[str] | str | None = None,
        evidence: list[str] | None = None,
        impact: float | None = None,
        type: str = "",
        confidence: str = "",
        scope: str = "auto",
        learning_id: str = "",
        status: str = "",
        metadata: dict[str, object] | str = "",
    ) -> LearnResultDict | dict[str, str]:
        """Use when capturing a discovery, or correcting one with learning_id.
        Routine observations dilute recall. Record root causes before fixing;
        label uncertainty; update, never duplicate.

        Create (no learning_id): summary + detail required. tags: list or
        comma/space string. impact 0-1, default 0.5. type:
        incident|pattern|convention|hypothesis|workaround. confidence:
        unverified|low|medium|high|verified. scope: auto|project|user.

        Update (learning_id): pass only what changes. status:
        active|resolved|obsolete. tags replace; "" or [] clears.

        metadata (unknown keys rejected). Create: source_type,
        source_identity, client_profile, model_id, consolidated_from,
        assertions, nudge_line, task_type, domain, phase_origin,
        phase_affinity, protection_tier. Update: tags_add (appends),
        supersedes (prior id; closes its window), reverify_anchors, expires,
        team_origin, assertions, nudge_line, task_type, domain, phase_origin,
        phase_affinity, protection_tier.

        Output: create: status (recorded, skipped/merged, rejected), learning_id,
        path. Update: status, learning_id, changes.

        See Also: trw_recall reads back.
        """
        # Maintainer notes (kept out of the docstring; callers pay for that text):
        #   One tool, two modes, chosen by learning_id (PRD-CORE-291-FR02). The
        #   update mode is the former trw_learn_update, whose name is gone with no
        #   alias: calling it is the client's unknown-tool error.
        #   run_path, shard_id, expires (create) and team_origin (create) were
        #   removed 2026-07-28; fastmcp raises ToolError on an unknown kwarg, so a
        #   caller passing a removed name fails loudly rather than silently.
        #   metadata's accepted keys per mode are _learn_arg_bags' two models.
        if learning_id:
            if evidence or scope != "auto":
                return {"error": "evidence and scope apply only when creating a learning", "status": "invalid"}
            upd, fields_reject = parse_learn_update_fields(metadata)
            if fields_reject is not None:
                return fields_reject
            return execute_learn_update(
                trw_dir=resolve_trw_dir(),
                config=get_config(),
                writer=FileStateWriter(),
                adapter_update=adapter_update,
                project_root=resolve_project_root,
                learning_id=learning_id,
                status=status or None,
                # None is "not named"; an explicit "" clears the field (FR03 patch contract).
                summary=summary,
                detail=detail,
                impact=impact,
                tags=tags,
                type=type or None,
                confidence=confidence or None,
                upd=upd,
            )
        if status:
            return {"status": "rejected", "reason": "invalid_arguments", "message": "status needs a learning_id"}
        from trw_mcp.state.source_detection import detect_client_profile, detect_model_id
        from trw_mcp.tools._learn_impl import execute_learn

        meta, meta_reject = parse_learn_metadata(metadata)
        if meta_reject is not None:
            return meta_reject
        # Coerce advertised type aliases (e.g. 'gotcha') before enum validation.
        type = _coerce_learn_type(type or "pattern")
        confidence = confidence or "unverified"
        enum_reject = _validate_learn_enums(type=type, confidence=confidence, protection_tier=meta.protection_tier)
        if enum_reject is not None:
            return enum_reject
        # None = "not provided" -> auto-detect; an explicit "" stays blank.
        client_profile = meta.client_profile if meta.client_profile is not None else detect_client_profile()
        model_id = (
            meta.model_id if meta.model_id is not None else detect_model_id(client_profile=client_profile or None)
        )
        call_ctx = _build_call_ctx(ctx)
        return execute_learn(
            summary=summary or "",
            detail=detail or "",
            trw_dir=resolve_trw_dir(),
            config=get_config(),
            tags=_coerce_tags(tags),
            evidence=evidence,
            impact=0.5 if impact is None else impact,
            source_type=meta.source_type,
            source_identity=meta.source_identity,
            client_profile=client_profile,
            model_id=model_id,
            consolidated_from=meta.consolidated_from,
            assertions=meta.assertions,
            is_solution_fn=_is_solution_summary,
            type=type,
            nudge_line=meta.nudge_line,
            confidence=confidence,
            task_type=meta.task_type,
            domain=meta.domain,
            phase_origin=meta.phase_origin,
            phase_affinity=meta.phase_affinity,
            protection_tier=meta.protection_tier,
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

    @server.tool()
    def trw_recall(
        ctx: Context | None = None,
        query: str = "",
        tags: list[str] | str | None = None,
        status: str | None = "active",
        max_results: int | None = None,
        ids: list[str] | None = None,
        options: dict[str, object] | str = "",
    ) -> RecallResultDict:
        """Retrieve prior learnings relevant to your current task.

        Use when entering unfamiliar code, when a bug may have been seen
        before, or to pull a narrow slice before delegating.

        Output: ranked stubs {id, claim, anchor}; omitted counts rows cut.

        query: keywords; "*" lists all. ids: full rows for these ids instead.
        tags: list or comma/space string. status: active (default) | resolved
        | obsolete. max_results defaults 25 (0 = unlimited).

        options (unknown keys rejected): topic slug, min_impact 0-1, as_of
        (ISO-8601), include_superseded, include_tiers (["project"] only).

        See Also: trw_learn to record a finding, trw_session_start for both at once.
        """
        # Maintainer notes (kept out of the docstring — callers pay for that text):
        #   Ranking = query relevance (summary/tags/detail) x utility (impact,
        #   type-aware recency decay), with context boosts for the
        #   caller's domain/phase/team. PRD-CORE-294 FR01: the presenter's byte
        #   budget bounds every default response. include_tiers is PRD-CORE-185 FR07 (project tier
        #   is the local source of truth, never excludable); as_of /
        #   include_superseded are the PRD-CORE-194 FR03 bi-temporal surface and a
        #   malformed as_of raises a clean validation error.
        #   shard_id was REMOVED (2026-07-27): it was declared, forwarded to
        #   execute_recall, and never read by anything in the body. A caller who
        #   passed it believed the recall was scoped to a shard and silently got
        #   the whole corpus — a WRONG RESULT, not an error. The docstring's
        #   "recorded for attribution" claim was also false: the only attribution
        #   sink (state.receipts.log_recall_receipt) is never called from this
        #   path, and its own shard_id kwarg has no production caller either.
        from trw_mcp.tools._recall_impl import execute_recall
        from trw_mcp.tools._tool_options import RecallOptions, parse_options

        opts = parse_options(RecallOptions, options)

        # PRD-CORE-141 FR03: build call_ctx so downstream find_active_run()
        # inside build_recall_context doesn't scan-hijack another session.
        call_ctx = _build_call_ctx(ctx)
        trw_dir = resolve_trw_dir()
        config = get_config()
        if ids:
            from trw_mcp.tools._recall_impl import recall_by_ids

            return recall_by_ids(trw_dir, config, ids, status=status)
        injected_ids = _read_injected_ids(trw_dir)
        return execute_recall(
            query=query,
            trw_dir=trw_dir,
            config=config,
            # PRD-IMPROVE-MCP-01 FR1: same tags coercion as trw_learn.
            tags=_coerce_tags(tags),
            min_impact=opts.min_impact,
            status=status,
            max_results=max_results,
            deprioritized_ids=injected_ids,
            topic=opts.topic,
            call_ctx=call_ctx,
            include_tiers=opts.include_tiers,
            as_of=opts.as_of,
            include_superseded=opts.include_superseded,
            # Dependency injection: pass module-level refs for testability
            _adapter_recall=adapter_recall,
        )

    @server.tool(output_schema=None)
    def trw_instructions_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
        dry_run: bool = False,
        force: bool = False,
    ) -> ClaudeMdSyncResultDict:
        """Sync TRW protocol and ceremony guidance into the client's instruction file.

        Use when onboarding a project whose instruction file (CLAUDE.md,
        AGENTS.md, or the client equivalent) lacks the TRW block, after a
        protocol-template change, or when switching IDE clients. Hand-written
        content is never truncated — a shrinking write is refused and reported.
        Learnings are not promoted into the instruction file — trw_session_start() recall covers that.

        Output: {status: "synced" | "unchanged" | "dry_run" | "refused", diffs, refusals}.

        Args:
            scope: "root" for the project instruction file, "sub" for module-level.
            target_dir: where to write the sub-scope file.
            client: "auto" detects from IDE config dirs, a specific client
                name targets its own file, "all" targets every known surface.
            dry_run: unified diff per target; writes nothing.
            force: write even if the guard detects content loss.
        """
        # Maintainer note: rendering targets the auto-generated block of whichever
        # client surface is present. Dropping learning promotion was PRD-CORE-093.
        # PRD-FIX-123-FR05: this entry point SUPPLIES the provenance trigger; it
        # is never inferred from a stack walk.
        config = get_config()
        reader = FileStateReader()
        llm = _create_llm_client()
        with instruction_write_trigger("tool_call", "trw_instructions_sync"):
            return execute_claude_md_sync(scope, target_dir, config, reader, llm, client, dry_run=dry_run, force=force)

    @server.tool(name="trw_claude_md_sync", output_schema=None)
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
        dry_run: bool = False,
        force: bool = False,
    ) -> ClaudeMdSyncResultDict:
        """Deprecated alias for ``trw_instructions_sync`` — call that instead.

        Use when an older caller still references this name; it warns on every
        invocation and will be removed.

        Output: same as trw_instructions_sync.
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
        with instruction_write_trigger("tool_call", "trw_claude_md_sync"):
            return execute_claude_md_sync(scope, target_dir, config, reader, llm, client, dry_run=dry_run, force=force)
