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

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts._ceremony import ClaudeMdSyncResultDict, ReviewMdResultDict
from trw_mcp.state.claude_md._agents_md import (
    _determine_write_target_decision,
    _sync_agents_md_if_needed,
    _sync_instruction_targets,
)
from trw_mcp.state.claude_md._parser import (
    load_claude_md_template,
    merge_trw_section,
    render_template,
)
from trw_mcp.state.claude_md._static_sections import (
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_imperative_opener,
    render_memory_harmonization,
    render_shared_learnings,
)
from trw_mcp.state.persistence import FileStateReader

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger(__name__)


def dispatch_for_profile(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    llm: "LLMClient",
    client: str = "auto",
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
            ``"opencode"``, ``"codex"``, ``"cursor"``, ``"aider"``,
            ``"gemini"``, ``"copilot"``, or ``"all"``).

    Returns:
        Dict shaped like :class:`ClaudeMdSyncResultDict` describing the
        sync outcome.
    """
    del reader, llm  # currently unused at dispatcher scope
    # Late-imports keep ``_profile_dispatcher`` importable before ``_sync`` is
    # fully initialised (legacy tests patch ``_sync.*`` module attributes).
    import trw_mcp.state.claude_md as _pkg
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

    trw_dir = _pkg.resolve_trw_dir()
    project_root = _pkg.resolve_project_root()

    # PRD-CORE-093 FR05: Hash excludes learning content — only package version
    # determines whether CLAUDE.md needs re-rendering. This keeps the prompt
    # cache stable across trw_deliver calls.
    if scope != "sub":
        current_hash = _compute_sync_hash()
        stored_hash = _read_stored_hash(trw_dir)
        if stored_hash is not None and stored_hash == current_hash:
            decision = _determine_write_target_decision(client, config, project_root, scope)
            instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_instruction_targets(
                project_root,
                decision.instruction_targets,
            )
            logger.debug("claude_md_sync_cache_hit", hash=current_hash[:12])
            logger.info(
                "claude_md_sync_skip",
                reason="no_changes",
            )
            target = project_root / "CLAUDE.md"
            agents_md_synced, agents_md_path = _sync_agents_md_if_needed(
                decision.write_agents,
                config,
                project_root,
                trw_dir,
                client=client,
                recall_fn=recall_learnings,
            )
            try:
                review_result = generate_review_md(trw_dir, repo_root=project_root)
            except Exception:  # justified: fail-open — REVIEW.md generation must not block cache-hit return
                logger.warning("review_md_generation_failed_cache_hit", exc_info=True)
                review_result = _review_md_failed_result("generation failed")
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
            )

    template = load_claude_md_template(trw_dir)

    # PRD-CORE-093 FR01/FR02: CLAUDE.md is the "always-on" prompt (loads every
    # message). Keep it compact — only the session_start trigger, ceremony quick
    # ref, memory routing, and closing reminder. Learning promotion removed;
    # full protocol delivered by session-start hook once per session event.
    tpl_context: dict[str, str] = {
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "memory_harmonization": render_memory_harmonization(),
        "shared_learnings": render_shared_learnings(),
        "closing_reminder": render_closing_reminder(),
    }

    trw_section = render_template(template, tpl_context)

    # PRD-CORE-061-FR04: Enforce max_auto_lines gate before writing
    auto_gen_lines = trw_section.count("\n")
    if auto_gen_lines > config.max_auto_lines:
        msg = (
            f"Auto-gen section is {auto_gen_lines} lines, "
            f"exceeds max_auto_lines={config.max_auto_lines}. "
            f"Refactor rendering before syncing."
        )
        raise StateError(msg)

    if scope == "sub" and target_dir:
        target = Path(target_dir).resolve() / "CLAUDE.md"
        max_lines = config.sub_claude_md_max_lines
    else:
        target = project_root / "CLAUDE.md"
        max_lines = config.claude_md_max_lines

    decision = _determine_write_target_decision(client, config, project_root, scope)
    write_claude = decision.write_claude
    write_agents = decision.write_agents

    total_lines = 0
    if write_claude:
        total_lines = merge_trw_section(target, trw_section, max_lines)

    update_analytics_sync(trw_dir)

    instruction_file_synced, instruction_file_path, instruction_file_paths = _sync_instruction_targets(
        project_root,
        decision.instruction_targets,
    )

    agents_md_synced, agents_md_path = _sync_agents_md_if_needed(
        write_agents,
        config,
        project_root,
        trw_dir,
        client=client,
        recall_fn=recall_learnings,
    )

    # Store hash after successful render (root scope only).
    if scope != "sub":
        rendered_hash = _compute_sync_hash()
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
        status="synced",
        total_lines=total_lines,
        agents_md_synced=agents_md_synced,
        agents_md_path=agents_md_path,
        instruction_file_synced=instruction_file_synced,
        instruction_file_path=instruction_file_path,
        instruction_file_paths=instruction_file_paths,
        review_md=review_md_result,
    )
