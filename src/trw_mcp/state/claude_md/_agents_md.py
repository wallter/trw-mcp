"""AGENTS.md sync — opencode-compatible instruction file generation.

Handles detection of which instruction files to write (CLAUDE.md, AGENTS.md)
and the actual generation + merge of AGENTS.md content.

Extracted from _sync.py (PRD-FIX-066 FR05) to keep the sync orchestrator
under the 500-line review threshold.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    merge_trw_section,
)
from trw_mcp.state.claude_md._review_md import (
    _sanitize_summary,
)
from trw_mcp.state.claude_md._review_md import (
    recall_learnings as _default_recall,
)

logger = structlog.get_logger(__name__)

# Type alias for recall function
RecallFn = Callable[..., list[dict[str, object]]]


def _determine_write_targets(
    client: str,
    config: TRWConfig,
    project_root: Path,
    scope: str,
) -> tuple[bool, bool]:
    """Determine whether to write CLAUDE.md and/or AGENTS.md based on client param.

    For known clients, delegates to the ClientProfile.write_targets model
    (single source of truth). The 'auto' and 'all' cases require runtime
    logic beyond what a static profile can express.

    Returns (write_claude, write_agents).
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    if client == "auto":
        from trw_mcp.bootstrap._utils import detect_ide

        ides = detect_ide(project_root)
        # cursor-only projects still benefit from CLAUDE.md content
        write_claude = "claude-code" in ides or not ides or (ides == ["cursor"])
        write_agents = any(ide in ides for ide in ("opencode", "codex")) and config.agents_md_enabled and scope == "root"
    elif client == "all":
        write_claude = True
        write_agents = config.agents_md_enabled and scope == "root"
    else:
        # Delegate to profile write_targets — single source of truth
        profile = resolve_client_profile(client)
        write_claude = profile.write_targets.claude_md
        write_agents = profile.write_targets.agents_md

    return write_claude, write_agents


def _inject_learnings_to_agents(
    trw_dir: Path,
    config: TRWConfig,
    recall_fn: RecallFn | None = None,
) -> str:
    """Build learning injection string for AGENTS.md or return empty string on error.

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration.
        recall_fn: Optional recall function override. Defaults to _review_md.recall_learnings.
            Callers (e.g., _sync.py) pass their own module-level recall_learnings so that
            tests patching ``_sync.recall_learnings`` propagate correctly.
    """
    _recall = recall_fn if recall_fn is not None else _default_recall
    try:
        learning_entries = _recall(
            trw_dir,
            min_impact=config.agents_md_learning_min_impact,
            status="active",
            max_results=config.agents_md_learning_max,
        )
        bullet_lines: list[str] = []
        for entry in learning_entries:
            summary = _sanitize_summary(str(entry.get("summary", "")))
            if summary:
                bullet_lines.append(f"- {summary}")
        if bullet_lines:
            return "\n## Key Learnings\n\n" + "\n".join(bullet_lines) + "\n"
    except Exception:  # justified: fail-open — learning injection is optional AGENTS.md enrichment
        logger.warning("agents_md_learning_injection_failed", exc_info=True)
    return ""


def _sync_agents_md_if_needed(
    write_agents: bool,
    config: TRWConfig,
    project_root: Path,
    trw_dir: Path,
    client: str = "auto",
    recall_fn: RecallFn | None = None,
) -> tuple[bool, str | None]:
    """Generate and write AGENTS.md if needed.

    Args:
        write_agents: Whether to write AGENTS.md.
        config: TRW configuration.
        project_root: Repository root path.
        trw_dir: Path to .trw directory.
        recall_fn: Optional recall function override for learning injection.

    Returns (agents_md_synced, agents_md_path).
    """
    if not write_agents:
        return False, None

    from trw_mcp.bootstrap._utils import detect_ide
    from trw_mcp.state.claude_md._static_sections import (
        render_agents_trw_section,
        render_codex_trw_section,
        render_minimal_protocol,
    )

    agents_target = project_root / "AGENTS.md"
    effective_client = client
    if client == "auto":
        detected_ides = detect_ide(project_root)
        if "codex" in detected_ides and "opencode" not in detected_ides:
            effective_client = "codex"

    if effective_client == "codex":
        agents_body = render_codex_trw_section()
    elif config.effective_ceremony_mode == "light":
        agents_body = render_minimal_protocol()
    else:
        agents_body = render_agents_trw_section()

    if config.agents_md_learning_injection:
        agents_body += _inject_learnings_to_agents(trw_dir, config, recall_fn=recall_fn)

    agents_section = f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{agents_body}\n{TRW_MARKER_END}\n"
    agents_lines = agents_section.count("\n")
    if agents_lines > config.max_auto_lines:
        logger.warning(
            "agents_md_section_oversized",
            lines=agents_lines,
            limit=config.max_auto_lines,
        )
    merge_trw_section(agents_target, agents_section, config.max_auto_lines)
    return True, str(agents_target)
