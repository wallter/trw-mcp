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
from trw_mcp.tools._learn_arg_bags import parse_learn_metadata, parse_learn_update_fields
from trw_mcp.tools._learning_module_helpers import _annotate_injected_learnings, _build_call_ctx, _coerce_tags
from trw_mcp.tools._learning_module_helpers import _coerce_learn_type, _is_solution_summary, _validate_learn_enums
from trw_mcp.tools._learning_module_helpers import _validate_learn_update_fields
from trw_mcp.tools._learning_module_helpers import _create_llm_client, _read_injected_ids
from trw_mcp.tools._learning_module_helpers import _sync_learning_yaml_backup
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
        type: str = "pattern",
        confidence: str = "unverified",
        source_type: str = "agent",
        scope: str = "auto",
        metadata: dict[str, object] | str = "",
    ) -> LearnResultDict:
        """Use when capturing discoveries. Routine observations dilute recall.

        Record root causes before fixing; label uncertainty. After validation,
        update the entry rather than duplicate it.

        Required: summary + detail (context, symptoms, significance).
        tags: list or comma/space string. impact: 0-1.
        type: incident | pattern | convention | hypothesis | workaround.
        confidence: unverified | low | medium | high | verified.
        scope: auto prefers the machine-local user store; project/user force one.

        metadata keys (unknown keys are rejected): source_identity, client_profile, model_id,
        consolidated_from, assertions, nudge_line, task_type, domain,
        phase_origin, phase_affinity, protection_tier. client_profile and
        model_id auto-detect when omitted.

        consolidated_from lists predecessor IDs to obsolete. Check
        consolidation_warning and recall: retirement can partially fail.

        Output: status, learning_id, path. Status: recorded, skipped/merged
        (dedup), or rejected with reason.

        See Also: trw_recall reads back; trw_learn_update corrects in place.
        """
        # Maintainer notes (kept out of the docstring — callers pay for that text):
        #   scope is PRD-CORE-185 FR07's write-tier override.
        #   run_path was REMOVED 2026-07-28 under explicit operator
        #   authorization. It was accepted and only debug-logged; it never
        #   changed storage, because learnings are run-independent. Removal is
        #   NOT free and the cost is recorded here rather than discovered later:
        #   fastmcp 3.2.4 raises ToolError("Unexpected keyword argument") on an
        #   unknown kwarg (verified empirically 2026-07-28), so an agent that
        #   carries run_path over from trw_checkpoint loses THAT call's
        #   learning. That failure is LOUD and self-correcting — the agent sees
        #   the error and can retry — which is the trade Truthfulness > Velocity
        #   accepts over a permanently-billed no-op parameter. fastmcp's
        #   exclude_args would prune the schema while still accepting the
        #   argument (also verified), but it is deprecated as of fastmcp 2.14
        #   and is not a foundation for a hot-path tool.
        #   metadata collapses 11 formerly-flat parameters (PRD-CORE-110 typed
        #   fields + PRD-CORE-099 provenance) into one object — see
        #   _learn_arg_bags for the accepted-key contract.
        #   shard_id / expires / team_origin were REMOVED here. Re-measured
        #   2026-07-28 against this repo's project store
        #   (.trw/memory/memory.db, 9,240 entries — the largest sample on hand,
        #   NOT a global census):
        #     shard_id    — not a column on `memories` at all, and absent from
        #                   all 1,959 non-empty metadata blobs. The 6,066 YAML
        #                   sidecars that carry a `shard_id:` key all carry it
        #                   EMPTY. So the argument was accepted and dropped: a
        #                   caller who passed it got no scoping and no error.
        #     expires_at  — 0 non-empty of 9,240.
        #     team_origin — 0 non-empty of 9,240.
        #   Read that as "never round-tripped in this corpus", not as proof no
        #   caller anywhere ever passed them. expires and team_origin remain
        #   settable through trw_learn_update(fields=...), which is where a TTL
        #   or an ownership correction is actually decided.
        # PRD-CORE-099: Auto-detect client and model when not explicitly provided.
        # None = "not provided" → auto-detect. Empty string = explicit blank.
        from trw_mcp.state.source_detection import detect_client_profile, detect_model_id
        from trw_mcp.tools._learn_impl import execute_learn

        meta, _meta_reject = parse_learn_metadata(metadata)
        if _meta_reject is not None:
            return _meta_reject

        # Potemkin defect C: coerce advertised type aliases (e.g. 'gotcha',
        # presented as first-class in the docstring + trw-deliver skill) to a
        # valid MemoryType BEFORE enum validation — genuine nonsense still
        # falls through to an honest rejection below.
        type = _coerce_learn_type(type)

        # core185-ENUM-UNGUARDED-3: validate enum args BEFORE forwarding so an
        # invalid value returns a structured rejection rather than an unhandled
        # ValueError from the downstream enum construction (mirrors trw_learn_update).
        _enum_reject = _validate_learn_enums(type=type, confidence=confidence, protection_tier=meta.protection_tier)
        if _enum_reject is not None:
            return _enum_reject

        client_profile = meta.client_profile if meta.client_profile is not None else detect_client_profile()
        model_id = meta.model_id if meta.model_id is not None else detect_model_id()
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
            source_type=source_type,
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

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_learn_update(
        ctx: Context | None = None,
        learning_id: str = "",
        status: str | None = None,
        summary: str | None = None,
        detail: str | None = None,
        impact: float | None = None,
        tags: list[str] | str | None = None,
        feedback: str | None = None,
        supersedes: str | None = None,
        reverify_anchors: bool = False,
        fields: dict[str, object] | str = "",
    ) -> dict[str, str]:
        """Update an existing learning — status, fields, or feedback signal.

        Use when a learning is fixed or stale, needs sharper text, or should be
        boosted/demoted in recall.

        Pass learning_id (e.g. "L-abc12345") plus only what changes; anything
        you omit is left untouched. status: active | resolved | obsolete (last
        two drop out of recall). feedback: helpful | unhelpful. tags (list or
        comma/space string) replace the existing set; [] clears it.

        supersedes: id of a PRIOR learning this replaces; closes its validity
        window (never a delete). reverify_anchors rechecks anchors against the
        current tree — use after a rename.

        fields is an optional object of typed attributes; unknown keys are
        rejected. Accepted: type (incident|pattern|convention|hypothesis|
        workaround), confidence (unverified|low|medium|high|verified), expires,
        nudge_line (cut at 80 chars), task_type, domain, phase_origin ("" or a
        phase name), phase_affinity, team_origin, protection_tier, assertions
        (replaces the set; [] clears it).

        Output: {status, learning_id, changes} — changes names what was
        written. status is "no_changes", "not_found", or "invalid" (with error).
        """
        # Maintainer notes (kept out of the docstring — callers pay for that text):
        #   assertions replacement is PRD-CORE-086 FR12; feedback feeds the
        #   feedback-aware decay of PRD-CORE-132; supersedes implements the
        #   bi-temporal window close of PRD-CORE-194 FR04 (sets invalid_from +
        #   invalidated_by on the prior record, never a delete); reverify_anchors
        #   is PRD-CORE-231 FR03 and is a no-op when the entry has no anchors.
        #   Callers own tag dedup/normalization. protection_tier accepts
        #   critical|high|normal|low|protected|permanent.
        #   PARTIAL-UPDATE SENTINEL: every typed field defaults to None and the
        #   adapter reads None as "the caller did not ask me to touch this". The
        #   `fields` bag preserves that exactly — an absent key AND an explicit
        #   null both yield None (see _learn_arg_bags.parse_learn_update_fields).
        #   Do not "helpfully" default a missing key to an empty value; that
        #   would silently clear data the caller never mentioned.
        config = get_config()
        writer = FileStateWriter()
        trw_dir = resolve_trw_dir()

        # PRD-IMPROVE-MCP-01 FR1, extended to the update path: agents routinely
        # pass tags="a,b,c" and trw_learn already accepts it, so rejecting the
        # identical value here was a live trap (hit 2026-09-10). Coerce ONLY the
        # string shape; a list is left untouched so the existing element-type
        # validation below still rejects e.g. ["ok", 42] — an update REPLACES the
        # tag set, and silently stringifying a stray int in a replace is worse
        # than failing loudly.
        if isinstance(tags, str):
            tags = _coerce_tags(tags)

        upd, _fields_reject = parse_learn_update_fields(fields)
        if _fields_reject is not None:
            return _fields_reject
        type, expires = upd.type, upd.expires
        nudge_line, confidence = upd.nudge_line, upd.confidence
        task_type, domain = upd.task_type, upd.domain
        phase_origin, phase_affinity = upd.phase_origin, upd.phase_affinity
        team_origin, protection_tier = upd.team_origin, upd.protection_tier
        assertions = upd.assertions

        # PRD-CORE-110: Validate enum fields before forwarding to adapter.
        # Potemkin defect C: coerce advertised type aliases (e.g. 'gotcha')
        # the same way trw_learn does, so the two tools share a type vocabulary.
        if type is not None:
            type = _coerce_learn_type(type)
        _reject = _validate_learn_update_fields(
            type=type,
            confidence=confidence,
            protection_tier=protection_tier,
            phase_origin=phase_origin,
            nudge_line=nudge_line,
            feedback=feedback,
            tags=tags,
        )
        if _reject is not None:
            return _reject

        # PRD-CORE-231-FR03: refresh anchor_validity against the current tree
        # BEFORE any other requested field update is applied.
        if reverify_anchors:
            from trw_mcp.tools._learn_anchors import reverify_entry_anchors

            reverify_entry_anchors(trw_dir, resolve_project_root(), learning_id)

        # Validate assertions before the owning-backend adapter persists them.
        validated_assertions: list[dict[str, object]] | None = None
        if assertions is not None:
            from trw_memory.models.memory import Assertion

            validated: list[Assertion] = [Assertion.model_validate(a, strict=False) for a in assertions]
            # mode="json": Assertion carries datetimes that a plain dump leaves
            # as objects, which breaks JSON serialization downstream.
            validated_assertions = [a.model_dump(mode="json") for a in validated]

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
            assertions=validated_assertions,
            feedback=feedback,
        )

        # Dual-write: also update YAML backup for rollback safety.
        if result.get("status") == "updated":
            logger.info("learn_update_ok", id=learning_id, changes=result.get("changes", ""))
            _sync_learning_yaml_backup(
                trw_dir,
                config,
                writer,
                learning_id,
                {
                    "status": status,
                    "detail": detail,
                    "summary": summary,
                    "impact": impact,
                    "assertions": validated_assertions,
                    # PRD-CORE-110 typed fields.
                    "type": type,
                    "nudge_line": nudge_line,
                    "expires": expires,
                    "confidence": confidence,
                    "task_type": task_type,
                    "domain": domain,
                    "phase_origin": phase_origin,
                    "phase_affinity": phase_affinity,
                    "team_origin": team_origin,
                    "protection_tier": protection_tier,
                    "tags": tags,
                },
            )

        return result

    @server.tool()
    @log_tool_call
    def trw_recall(
        ctx: Context | None = None,
        query: str = "",
        tags: list[str] | str | None = None,
        min_impact: float = 0.0,
        status: str | None = "active",
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

        Use when entering unfamiliar code, when a bug may have been seen
        before, or to pull a narrow slice before delegating.

        Output: relevance-ranked learnings and a count.

        Shaping: compact trims fields (auto-on for "*"), ultra_compact leaves
        id+summary only. token_budget (>0) budgets learning entries (minimum one),
        not metadata/advisories. max_results defaults to 25 (0 = unlimited). Filters: tags (list or comma/space string), topic slug, min_impact 0.0-1.0,
        as_of (ISO-8601 instant: returns records whose validity window covered
        it; omitted means open records only), include_superseded (ranked below
        open records).

        See Also: trw_learn to record a finding, trw_session_start for both at once.

        Args:
            query: Keywords matched against summaries/details; "*" lists all.
            status: 'active' (default), 'resolved', or 'obsolete'.
            include_tiers: Project entries always return; this only toggles the
                machine-local user tier — ["project"] excludes it, omitting it
                or including "user" adds it. User-only is not expressible.
        """
        # Maintainer notes (kept out of the docstring — callers pay for that text):
        #   Ranking = query relevance (summary/tags/detail) x utility (impact,
        #   type-aware recency decay, prior feedback), with context boosts for the
        #   caller's domain/phase/team. token_budget's implicit default cap is the
        #   anti-collapse guard. include_tiers is PRD-CORE-185 FR07 (project tier
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

        # PRD-CORE-141 FR03: build call_ctx so downstream find_active_run()
        # inside build_recall_context doesn't scan-hijack another session.
        call_ctx = _build_call_ctx(ctx)
        trw_dir = resolve_trw_dir()
        injected_ids = _read_injected_ids(trw_dir)
        # Resolve from this module's namespace so test patches work
        from trw_mcp.tools._interactive_recall import prepare_interactive_recall

        config = get_config()
        interactive_adapter, retrieval_warning = prepare_interactive_recall(
            adapter_recall, embeddings_enabled=config.embeddings_enabled, query=query
        )
        result = execute_recall(
            query=query,
            trw_dir=trw_dir,
            config=config,
            # PRD-IMPROVE-MCP-01 FR1: same tags coercion as trw_learn.
            tags=_coerce_tags(tags),
            min_impact=min_impact,
            status=status,
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
            _adapter_recall=interactive_adapter,
            _adapter_update_access=adapter_update_access,
            _search_patterns=search_patterns,
            _rank_by_utility=rank_by_utility,
            _collect_context=collect_context,
        )
        if retrieval_warning:
            result["retrieval_warning"] = retrieval_warning

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
        dry_run: bool = False,
        force: bool = False,
    ) -> ClaudeMdSyncResultDict:
        """Sync TRW protocol and ceremony guidance into the client's instruction file.

        Use when onboarding a project whose instruction file — CLAUDE.md,
        AGENTS.md, or the equivalent for the active client — lacks the TRW
        auto-generated block, after changing the protocol template, or when
        switching IDE clients. Your hand-written content is never truncated: a
        write that would shrink it is refused and reported.
        Learnings are not promoted into the instruction file — trw_session_start() recall covers that.

        Output: {status: "synced" | "unchanged" | "dry_run" | "refused", diffs, refusals}.

        Args:
            scope: "root" for the project instruction file, "sub" for module-level.
            target_dir: Directory to write the sub-scope file into.
            client: "auto" (detect from IDE config dirs), "claude-code"
                (CLAUDE.md), "opencode" (AGENTS.md), "codex"
                (.codex/INSTRUCTIONS.md), or "all" for every known surface.
            dry_run: Return a unified diff per target and write nothing.
            force: Write even when the guard measures a loss of your content.
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
    @log_tool_call
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
        client: str = "auto",
        dry_run: bool = False,
        force: bool = False,
    ) -> ClaudeMdSyncResultDict:
        """Deprecated alias for ``trw_instructions_sync`` — call that instead.

        Use when an older caller still references this name; it warns on every
        invocation and will be removed in a future release.

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
