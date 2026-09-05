"""PRD-CORE-149-FR11: per-profile sync dispatcher.

Owns the profile-aware routing logic for ``execute_claude_md_sync``. Hash /
invalidation helpers, REVIEW.md generation, and the shared result-shape
builder remain in ``_sync.py`` (tests patch ``_sync.recall_learnings`` and
``_sync.tempfile`` at the module level, so those symbols must not move).

The public entry point ``execute_claude_md_sync`` is re-exported from
``_sync.py`` for backward compatibility; callers may also import
``dispatch_for_profile`` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts._ceremony import (
    ClaudeMdSyncResultDict,
    InstructionDiffDict,
    InstructionPointerSkipDict,
    InstructionWriteRefusalDict,
    ReviewMdResultDict,
)
from trw_mcp.state.claude_md._agents_md import (
    _determine_write_target_decision,
    _sync_agents_md_if_needed,
    _sync_instruction_targets,
)
from trw_mcp.state.claude_md._profile_dispatch_report import (
    _cache_hit_carrier_report as _cache_hit_carrier_report,
)
from trw_mcp.state.claude_md._profile_dispatch_report import (
    _capability_parity_drift as _capability_parity_drift,
)
from trw_mcp.state.claude_md._profile_render import render_profile_section
from trw_mcp.state.persistence import FileStateReader

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger(__name__)


def dispatch_for_profile(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    llm: LLMClient,
    client: str = "auto",
    instruction_manifest_hashes: dict[str, str] | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> ClaudeMdSyncResultDict:
    """Dispatch a CLAUDE.md / AGENTS.md sync for the active profile.

    PRD-CORE-149-FR11: profile-routing logic lifted from ``_sync.py`` so the
    monolithic sync file stays under the 350-LOC ceiling. The function
    orchestrates: hash cache lookup, per-profile write-target decision,
    template render, ``CLAUDE.md`` / ``AGENTS.md`` write, and REVIEW.md
    regeneration.

    Unknown or unrecognized ``client`` values route through the same
    ``_determine_write_target_decision`` logic as supported profiles and
    therefore default to the ``claude-code`` behaviour (write CLAUDE.md).

    Args:
        scope: Sync scope -- ``"root"`` or ``"sub"``.
        target_dir: Target directory for sub-scope rendering.
        config: Active TRW configuration.
        reader: File state reader (unused but preserved for API parity).
        llm: LLM client (unused at the dispatcher layer; retained for
            parity with the upstream tool signature).
        client: Target client identifier (``"auto"``, ``"claude-code"``,
            ``"opencode"``, ``"codex"``, ``"cursor"``, ``"copilot"``,
            ``"antigravity-cli"``, or ``"all"``).
        instruction_manifest_hashes: Content-hash baseline describing TRW's
            last write to the per-client instruction files, captured before any
            write in the calling flow. ``update-project`` must pass this,
            because it rewrites the on-disk manifest from current content
            before reaching here — leaving a user's edit indistinguishable from
            TRW's own output. ``None`` lets the generators read the manifest
            themselves, which is correct for a standalone sync.
        dry_run: Compute what each target WOULD receive, return a unified diff
            per target, and write nothing (PRD-FIX-123-FR03).
        force: Bypass the write guard's shrink floors. A call argument only —
            never a config field (PRD-FIX-123-FR02).

    Returns:
        Dict shaped like :class:`ClaudeMdSyncResultDict` describing the
        sync outcome.
    """
    del reader, llm  # currently unused at dispatcher scope
    # Late-imports keep ``_profile_dispatcher`` importable before ``_sync`` is
    # fully initialised (legacy tests patch ``_sync.*`` module attributes).
    from trw_mcp.state import _paths
    from trw_mcp.state.analytics import update_analytics_sync
    from trw_mcp.state.claude_md._sync import (
        _build_sync_result,
        _compute_sync_hash,
        _read_stored_hash,
        _review_md_failed_result,
        _write_stored_hash,
        generate_review_md,
        recall_learnings,
    )

    # PRD: resolve the write target via LATE lookup through ``_paths`` so the
    # functions are read at call time, not bound at import. This makes the sync
    # honour ``monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root")``
    # and any runtime ``chdir`` — and removes the import-time-binding fragility
    # that previously let tests pollute the real repo CLAUDE.md.
    trw_dir = _paths.resolve_trw_dir()
    project_root = _paths.resolve_project_root()

    # Refresh before the hash-cache return so profile switches cannot leave
    # stale hook flags behind when instruction prose is otherwise unchanged.
    from trw_mcp.state.claude_md._hook_policy import refresh_hook_policy

    refresh_hook_policy(trw_dir, project_root, config, client)

    # PRD-CORE-093 FR05: Hash excludes learning content — only package version
    # determines whether CLAUDE.md needs re-rendering. This keeps the prompt
    # cache stable across trw_deliver calls.
    if scope != "sub":
        current_hash = _compute_sync_hash(config)
        stored_hash = _read_stored_hash(trw_dir)
        # ``force=True`` must bypass the cache-hit early return. Before this
        # fix, a hash match reported "unchanged" and returned unconditionally
        # regardless of ``force``, so a caller asking to force-regenerate an
        # unchanged-hash instruction file (via the MCP tool or the CLI) got a
        # silent no-op — the write guard never ran, `apply_carrier` was never
        # called, and the on-disk file was never touched.
        if force and stored_hash is not None and stored_hash == current_hash:
            logger.info(
                "claude_md_sync_force_bypasses_cache_hit",
                hash=current_hash[:12],
            )
        if not force and stored_hash is not None and stored_hash == current_hash:
            decision = _determine_write_target_decision(client, config, project_root, scope)
            instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_instruction_targets(
                project_root,
                decision.instruction_targets,
                instruction_manifest_hashes,
            )
            logger.debug("claude_md_sync_cache_hit", hash=current_hash[:12])
            logger.info(
                "claude_md_sync_skip",
                reason="no_changes",
            )
            target = project_root / "CLAUDE.md"
            agents_md_synced, agents_md_path, agents_verdict = _sync_agents_md_if_needed(
                decision.write_agents,
                config,
                project_root,
                trw_dir,
                client=client,
                recall_fn=recall_learnings,
                force=force,
                dry_run=dry_run,
            )
            del agents_verdict  # cache-hit path reports no diff/refusal payload
            try:
                review_result = generate_review_md(trw_dir, repo_root=project_root)
            except Exception:  # justified: fail-open — REVIEW.md generation must not block cache-hit return
                logger.warning("review_md_generation_failed_cache_hit", exc_info=True)
                review_result = _review_md_failed_result("generation failed")
            # PRD-CORE-203 FR07 (P1-1): report the carrier state even on a cache
            # hit (no write happens, so this is a read-only classification of the
            # current CLAUDE.md).
            cm, ps, ep = _cache_hit_carrier_report(target, decision.write_claude, config, scope)
            return _build_sync_result(
                path=str(target),
                scope=scope,
                status="unchanged",
                total_lines=0,
                agents_md_synced=agents_md_synced,
                agents_md_path=agents_md_path,
                instruction_file_synced=instruction_file_synced,
                instruction_file_path=instruction_file_path,
                instruction_file_paths=instruction_file_paths,
                review_md=review_result,
                hash_value=current_hash,
                carrier_mode=cm,
                pointer_skips=ps,
                external_path=ep,
                capability_parity_drift=_capability_parity_drift(decision.write_agents, client),
            )

    if scope == "sub" and target_dir:
        target = Path(target_dir).resolve() / "CLAUDE.md"
        max_lines = config.sub_claude_md_max_lines
    else:
        target = project_root / "CLAUDE.md"
        max_lines = config.claude_md_max_lines

    # Resolved BEFORE the render: PRD-FIX-123-FR07's gate measures the merged
    # total, which needs the target it would merge into — and the budget, so a
    # sub-scope section that overflows collapses to the pointer form rather than
    # having the writer refuse TRW's own output (``_section_budget``).
    trw_section = render_profile_section(trw_dir, project_root, config, target, max_lines=max_lines, scope=scope)

    decision = _determine_write_target_decision(client, config, project_root, scope)
    write_claude = decision.write_claude
    write_agents = decision.write_agents

    total_lines = 0
    carrier_mode: str | None = None
    pointer_skips: list[InstructionPointerSkipDict] | None = None
    external_path: str | None = None
    refusals: list[InstructionWriteRefusalDict] = []
    diffs: list[InstructionDiffDict] = []
    if write_claude:
        # PRD-CORE-203 FR05/FR06/FR07: resolve the carrier for CLAUDE.md. It is
        # Claude Code's instruction file, so import-capability comes from the
        # claude-code profile regardless of the requested ``client`` (client="all"
        # still writes CLAUDE.md for Claude Code). A single-source pointer is
        # healed + left un-clobbered; an import-capable target is externalized to
        # the ``.trw`` sidecar (inline fallback on failure); else inline.
        from trw_mcp.models.config._profiles import resolve_client_profile
        from trw_mcp.state.claude_md._instruction_carrier import CarrierMode, apply_carrier

        outcome = apply_carrier(
            target,
            trw_section,
            max_lines,
            import_syntax=resolve_client_profile("claude-code").instruction_import_syntax,
            externalize=config.instruction_externalize,
            scope=scope,
            external_filename=config.instruction_external_filename,
            project_root=project_root,
            force=force,
            dry_run=dry_run,
        )
        total_lines = outcome.total_lines
        if outcome.refusal is not None:
            refusals.append(outcome.refusal)
        if outcome.diff is not None:
            diffs.append(outcome.diff)
        carrier_mode = outcome.mode.value
        external_path = outcome.external_path
        if outcome.mode is CarrierMode.POINTER_SKIP:
            pointer_skips = [
                {
                    "path": str(target),
                    "import_targets": list(outcome.pointer_targets),
                    "healed": outcome.healed,
                }
            ]

    update_analytics_sync(trw_dir)

    instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_instruction_targets(
        project_root,
        decision.instruction_targets,
        instruction_manifest_hashes,
    )

    agents_md_synced, agents_md_path, agents_verdict = _sync_agents_md_if_needed(
        write_agents,
        config,
        project_root,
        trw_dir,
        client=client,
        recall_fn=recall_learnings,
        force=force,
        dry_run=dry_run,
    )
    if agents_verdict is not None and agents_verdict.refusal is not None:
        refusals.append(agents_verdict.refusal)
    if agents_verdict is not None and agents_verdict.diff is not None:
        diffs.append(agents_verdict.diff)

    # Store hash after successful render (root scope only). A dry run and a
    # refused write must NOT record the hash: doing so would make the next real
    # sync a cache hit and silently skip the write that never happened.
    if scope != "sub" and not dry_run and not refusals:
        rendered_hash = _compute_sync_hash(config)
        _write_stored_hash(trw_dir, rendered_hash)

    # PRD-CORE-084 FR08: Generate REVIEW.md after CLAUDE.md sync completes.
    review_md_result: ReviewMdResultDict
    try:
        review_md_result = generate_review_md(trw_dir, repo_root=project_root)
    except Exception:  # justified: fail-open — REVIEW.md failure must not block CLAUDE.md sync
        logger.warning("review_md_generation_failed", exc_info=True)
        review_md_result = _review_md_failed_result("generation failed")

    logger.info(
        "claude_md_sync_ok",
        scope=scope,
        path=str(target),
        client=client,
        write_claude=write_claude,
        write_agents=write_agents,
    )
    logger.debug(
        "claude_md_sync_detail",
        total_lines=total_lines,
        agents_md_path=agents_md_path if agents_md_synced else None,
        instruction_file_paths=instruction_file_paths,
    )
    return _build_sync_result(
        path=str(target),
        scope=scope,
        status="dry_run" if dry_run else ("refused" if refusals else "synced"),
        total_lines=total_lines,
        agents_md_synced=agents_md_synced,
        agents_md_path=agents_md_path,
        instruction_file_synced=instruction_file_synced,
        instruction_file_path=instruction_file_path,
        instruction_file_paths=instruction_file_paths,
        review_md=review_md_result,
        carrier_mode=carrier_mode,
        pointer_skips=pointer_skips,
        external_path=external_path,
        capability_parity_drift=_capability_parity_drift(write_agents, client),
        diffs=diffs if dry_run else None,
        refusals=refusals or None,
    )
