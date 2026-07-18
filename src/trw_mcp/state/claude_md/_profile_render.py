"""Compact render and size-gate helper for profile instruction dispatch."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._agents_md import _enforce_size_gate, _resolve_size_gate_mode
from trw_mcp.state.claude_md._parser import load_claude_md_template, render_template
from trw_mcp.state.claude_md._static_sections import (
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_imperative_opener,
    render_memory_harmonization,
    render_shared_learnings,
)
from trw_mcp.state.claude_md.sections._feedback import render_feedback_reporting


def render_profile_section(trw_dir: Path, project_root: Path, config: TRWConfig) -> str:
    """Render the compact always-on section and enforce its brownfield size gate."""
    template = load_claude_md_template(trw_dir)
    context = {
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "memory_harmonization": render_memory_harmonization(),
        "shared_learnings": render_shared_learnings(),
        "feedback_reporting": render_feedback_reporting(config.client_profile),
        "closing_reminder": render_closing_reminder(),
    }
    section = render_template(template, context)
    lines = section.count("\n")
    oversized = _enforce_size_gate(
        file_label="CLAUDE.md",
        lines=lines,
        limit=config.max_auto_lines,
        mode=_resolve_size_gate_mode(config, project_root),
    )
    if oversized is not None:
        raise StateError(
            f"Auto-gen section is {lines} lines, exceeds max_auto_lines={config.max_auto_lines}. "
            "Refactor rendering before syncing."
        )
    return section


__all__ = ["render_profile_section"]
