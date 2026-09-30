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
    InstructionFileTarget,
    _determine_write_target_decision,
    _sync_agents_md_if_needed,
    _sync_instruction_targets,
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
    """Dispatch an AGENTS.md / per-client instruction sync for the active profile.

    PRD-CORE-149-FR11: profile-routing logic lifted from ``_sync.py`` so the
    monolithic sync file stays under the 350-LOC ceiling. The function
    orchestrates: hash cache lookup, per-profile write-target decision,
    template render, ``AGENTS.md`` write, and REVIEW.md regeneration. TRW
    no longer writes ``CLAUDE.md``: Claude Code reads ``AGENTS.md`` natively.

    Unknown or unrecognized ``client`` values route through the same
    ``_determine_write_target_decision`` logic as supported profiles and
    therefore default to the ``claude-code`` behaviour (write AGENTS.md).

    Args:
        scope: Sync scope -- ``"root"`` (project ``AGENTS.md``) or ``"sub"``
            (``AGENTS.md`` in *target_dir*).
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
        dry_run: Compute what AGENTS.md WOULD receive, return a
            unified diff per target, and write nothing (PRD-FIX-123-FR03). The
            per-client carriers, REVIEW.md, analytics and the hook env file
            have no diff mode, so a dry run skips them (B71-110).
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

    # B71-110: a dry run writes nothing, so it also leaves the hook env file,
    # the per-client carriers, analytics and REVIEW.md alone.
    hook_env_warnings: list[str] = []
    if not dry_run:
        hook_env_warnings = refresh_hook_policy(trw_dir, project_root, config, client)

    def _sync_carriers(targets: tuple[InstructionFileTarget, ...]) -> tuple[bool, str | None, list[str]]:
        # The per-client generators have no diff mode: a dry run reports them
        # as not synced instead of writing them.
        if dry_run:
            return False, None, []
        return _sync_instruction_targets(project_root, targets, instruction_manifest_hashes)

    def _review_md(failure_event: str) -> ReviewMdResultDict:
        if dry_run:
            return {"status": "skipped", "path": None, "rules_count": 0}
        try:
            return generate_review_md(trw_dir, repo_root=project_root, allow_empty=force, force=force)
        except Exception:  # justified: fail-open — REVIEW.md generation must not block the sync
            logger.warning(failure_event, exc_info=True)
            return _review_md_failed_result("generation failed")

    # PRD-CORE-093 FR05: Hash excludes learning content — only package version
    # determines whether CLAUDE.md needs re-rendering. This keeps the prompt
    # cache stable across trw_deliver calls.
    if scope != "sub":
        current_hash = _compute_sync_hash()
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
        # A dry run also skips the cache hit: it must render to report a diff.
        if not force and not dry_run and stored_hash is not None and stored_hash == current_hash:
            decision = _determine_write_target_decision(client, config, project_root, scope)
            instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_carriers(
                decision.instruction_targets
            )
            logger.debug("claude_md_sync_cache_hit", hash=current_hash[:12])
            logger.info(
                "claude_md_sync_skip",
                reason="no_changes",
            )
            target = project_root / "AGENTS.md"
            agents_md_synced, agents_md_path, agents_verdicts = _sync_agents_md_if_needed(
                decision.write_agents,
                config,
                project_root,
                client=client,
                force=force,
                dry_run=dry_run,
            )
            del agents_verdicts  # cache-hit path reports no diff/refusal payload
            review_result = _review_md("review_md_generation_failed_cache_hit")
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
                capability_parity_drift=_capability_parity_drift(decision.write_agents, client),
                warnings=hook_env_warnings or None,
            )

    sub_scope = scope == "sub" and bool(target_dir)
    target = (Path(target_dir).resolve() if sub_scope and target_dir else project_root) / "AGENTS.md"

    decision = _determine_write_target_decision(client, config, project_root, scope)
    write_agents = decision.write_agents

    total_lines = 0
    carrier_mode: str | None = None
    pointer_skips: list[InstructionPointerSkipDict] | None = None
    refusals: list[InstructionWriteRefusalDict] = []
    diffs: list[InstructionDiffDict] = []
    if sub_scope:
        # A module-level AGENTS.md (Claude Code loads a subdirectory's AGENTS.md
        # on demand). Rendered against its target so PRD-FIX-123-FR07's gate
        # measures the merged total and an overflowing section collapses to the
        # pointer form (``_section_budget``). A single-source pointer is healed
        # and left un-clobbered (PRD-CORE-203 FR04/FR06).
        from trw_mcp.state.claude_md._instruction_carrier import CarrierMode, apply_carrier

        max_lines = config.sub_claude_md_max_lines
        trw_section = render_profile_section(trw_dir, project_root, config, target, max_lines=max_lines, scope=scope)
        outcome = apply_carrier(target, trw_section, max_lines, force=force, dry_run=dry_run)
        total_lines = outcome.total_lines
        if outcome.refusal is not None:
            refusals.append(outcome.refusal)
        if outcome.diff is not None:
            diffs.append(outcome.diff)
        carrier_mode = outcome.mode.value
        if outcome.mode is CarrierMode.POINTER_SKIP:
            pointer_skips = [
                {
                    "path": str(target),
                    "import_targets": list(outcome.pointer_targets),
                    "healed": outcome.healed,
                }
            ]

    if not dry_run:
        update_analytics_sync(trw_dir)

    instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_carriers(
        decision.instruction_targets
    )

    agents_md_synced, agents_md_path, agents_verdicts = _sync_agents_md_if_needed(
        write_agents,
        config,
        project_root,
        client=client,
        force=force,
        dry_run=dry_run,
    )
    for agents_verdict in agents_verdicts:
        if agents_verdict.refusal is not None:
            refusals.append(agents_verdict.refusal)
        if agents_verdict.diff is not None:
            diffs.append(agents_verdict.diff)

    # Store hash after successful render (root scope only). A dry run and a
    # refused write must NOT record the hash: doing so would make the next real
    # sync a cache hit and silently skip the write that never happened.
    if scope != "sub" and not dry_run and not refusals:
        rendered_hash = _compute_sync_hash()
        _write_stored_hash(trw_dir, rendered_hash)

    # PRD-CORE-084 FR08: Generate REVIEW.md after the instruction sync completes.
    review_md_result = _review_md("review_md_generation_failed")

    logger.info(
        "claude_md_sync_ok",
        scope=scope,
        path=str(target),
        client=client,
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
        capability_parity_drift=_capability_parity_drift(write_agents, client),
        diffs=diffs if dry_run else None,
        refusals=refusals or None,
        warnings=hook_env_warnings or None,
    )
