"""Compact render and size-gate helper for profile instruction dispatch."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._agents_md import _enforce_size_gate, _resolve_size_gate_mode
from trw_mcp.state.claude_md._parser import (
    load_claude_md_template,
    render_template,
)
from trw_mcp.state.claude_md._section_budget import fit_section_to_budget, merged_line_count
from trw_mcp.state.claude_md._static_sections import (
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_imperative_opener,
    render_memory_harmonization,
    render_shared_learnings,
)
from trw_mcp.state.claude_md.sections._feedback import render_feedback_reporting


def render_profile_section(
    trw_dir: Path,
    project_root: Path,
    config: TRWConfig,
    target: Path,
    *,
    max_lines: int | None = None,
    scope: str = "root",
) -> str:
    """Render the compact always-on section and enforce its brownfield size gate.

    *target* is the instruction file the section will be merged into. PRD-FIX-123-FR07:
    the gate measures the MERGED total, because that is the quantity the writer
    enforces ``max_auto_lines`` on — measuring the rendered section alone reported
    "safe" while the writer went on to truncate hand-written content.

    *max_lines* is the budget the writer will enforce for *target* and *scope* is
    the sync scope; together they let :func:`fit_section_to_budget` collapse a
    sub-scope section that overflows its own budget to the pointer form, instead
    of the writer refusing TRW's own output. The size gate below then measures
    the section that will actually be written.
    """
    template = load_claude_md_template(trw_dir)
    context = {
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "memory_harmonization": render_memory_harmonization(),
        "shared_learnings": render_shared_learnings(),
        "feedback_reporting": render_feedback_reporting(config.client_profile),
        "closing_reminder": render_closing_reminder(),
    }
    section = fit_section_to_budget(target, render_template(template, context), max_lines, scope=scope)
    # ``max`` preserves the render-side protection: a section that alone exceeds
    # the limit is still oversize even when the target does not exist yet.
    lines = max(section.count("\n"), merged_line_count(target, section))
    oversized = _enforce_size_gate(
        file_label="CLAUDE.md",
        lines=lines,
        limit=config.max_auto_lines,
        mode=_resolve_size_gate_mode(config, project_root),
    )
    if oversized is not None:
        raise StateError(
            f"Merged {target.name} would be {lines} lines, exceeding max_auto_lines={config.max_auto_lines}. "
            "Raise max_auto_lines or shorten the file — TRW will not truncate your content."
        )
    return section


__all__ = ["render_profile_section"]
