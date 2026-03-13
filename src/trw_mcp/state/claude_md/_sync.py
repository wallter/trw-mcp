"""CLAUDE.md sync orchestration — coordinates promotion, rendering, and merge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._parser import (
    load_claude_md_template,
    merge_trw_section,
    render_template,
)
from trw_mcp.state.claude_md._promotion import (
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
)
from trw_mcp.state.claude_md._static_sections import (
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_framework_reference,
    render_imperative_opener,
)
from trw_mcp.state.claude_md._templates import (
    CLAUDEMD_LEARNING_CAP,
    CLAUDEMD_PATTERN_CAP,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger()


def execute_claude_md_sync(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
    llm: LLMClient,
    client: str = "auto",
) -> dict[str, object]:
    """Execute the CLAUDE.md sync operation.

    Core logic extracted from the ``trw_claude_md_sync`` tool to keep
    ``tools/learning.py`` under 400 lines (Sprint 12 GAP-FR-001).

    Args:
        scope: Sync scope -- "root" or "sub".
        target_dir: Target directory for sub-CLAUDE.md generation.
        config: TRW configuration.
        reader: File state reader.
        writer: File state writer.
        llm: LLM client instance.
        client: Target client(s) to write instructions for.
            "auto" (default) -- detect via IDE config dirs;
            "claude-code" -- write CLAUDE.md only;
            "opencode" -- write AGENTS.md only;
            "all" -- write both CLAUDE.md and AGENTS.md.

    Returns:
        Result dictionary with sync metadata.
    """
    import trw_mcp.state.claude_md as _pkg

    from trw_mcp.state.analytics import mark_promoted, update_analytics_sync
    from trw_mcp.state.llm_helpers import llm_summarize_learnings

    trw_dir = _pkg.resolve_trw_dir()
    project_root = _pkg.resolve_project_root()

    high_impact = collect_promotable_learnings(trw_dir, config, reader)
    patterns = collect_patterns(trw_dir, config, reader)
    _arch_data, _conv_data = collect_context_data(trw_dir, config, reader)

    llm_summary: str | None = None
    if (high_impact or patterns) and config.llm_enabled and llm.available:  # pragma: no cover
        llm_summary = llm_summarize_learnings(
            high_impact, patterns, llm, CLAUDEMD_LEARNING_CAP, CLAUDEMD_PATTERN_CAP,
        )

    template = load_claude_md_template(trw_dir)

    # PRD-CORE-061: Progressive disclosure — suppress ceremony/behavioral/learnings
    # sections from CLAUDE.md. These are now delivered via:
    # - /trw-ceremony-guide skill (on-demand)
    # - session-start.sh hook (behavioral protocol, one-time)
    # - trw_session_start() recall (learnings)
    tpl_context: dict[str, str] = {
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "framework_reference": render_framework_reference(),
        "closing_reminder": render_closing_reminder(),
        # Suppressed — moved to /trw-ceremony-guide skill
        "behavioral_protocol": "",
        "delegation_section": "",
        "agent_teams_section": "",
        "rationalization_watchlist": "",
        "ceremony_phases": "",
        "ceremony_table": "",
        "ceremony_flows": "",
        # Suppressed — learnings delivered via trw_session_start() recall
        "architecture_section": "",
        "conventions_section": "",
        "categorized_learnings": "",
        "patterns_section": "",
        "adherence_section": "",
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

    # FR13: Determine write targets based on client parameter.
    # "auto" uses detect_ide() to check for .opencode/ or opencode.json.
    # The legacy config.agents_md_enabled flag is respected only in auto/all modes.
    write_claude: bool = True
    write_agents: bool = False

    if client == "auto":
        from trw_mcp.bootstrap._utils import detect_ide

        ides = detect_ide(project_root)
        write_claude = "claude-code" in ides or not ides  # default to claude when no IDE found
        # Write AGENTS.md if opencode detected, gated by agents_md_enabled config flag
        write_agents = "opencode" in ides and config.agents_md_enabled and scope == "root"
    elif client == "claude-code":
        write_claude, write_agents = True, False
    elif client == "opencode":
        write_claude, write_agents = False, True
    elif client == "all":
        write_claude = True
        write_agents = config.agents_md_enabled and scope == "root"
    # else: fallback to default (write_claude=True, write_agents=False)

    total_lines = 0
    if write_claude:
        total_lines = merge_trw_section(target, trw_section, max_lines)

    update_analytics_sync(trw_dir)

    for learning in high_impact:
        lid = learning.get("id", "")
        if isinstance(lid, str) and lid:
            mark_promoted(trw_dir, lid)

    # FR13: Write AGENTS.md with identical TRW section when opencode is targeted.
    agents_md_synced = False
    agents_md_path: str | None = None
    if write_agents:
        agents_target = project_root / "AGENTS.md"
        merge_trw_section(agents_target, trw_section, max_lines)
        agents_md_synced = True
        agents_md_path = str(agents_target)

    logger.info(
        "trw_claude_md_synced",
        scope=scope,
        target=str(target),
        learnings_promoted=len(high_impact),
        patterns_included=len(patterns),
        client=client,
        write_claude=write_claude,
        write_agents=write_agents,
        agents_md_path=agents_md_path if agents_md_synced else None,
    )
    return {
        "path": str(target),
        "scope": scope,
        "status": "synced",
        "learnings_promoted": len(high_impact),
        "patterns_included": len(patterns),
        "total_lines": total_lines,
        "llm_used": llm_summary is not None,
        "agents_md_synced": agents_md_synced,
        "agents_md_path": agents_md_path,
        "bounded_contexts_synced": 0,
    }
