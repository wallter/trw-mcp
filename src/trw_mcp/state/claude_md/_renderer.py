"""Unified renderer for TRW protocol instructions.

PRD-CORE-131: Centralizes all ceremony guidance generation into a single
``ProtocolRenderer`` class, replacing hardcoded strings scattered across
``_static_sections.py``, ``_opencode_sections.py``, and ``_gemini.py``.

The renderer is parameterized by ``ClientProfile``, ``model_family``, and
``ceremony_mode`` (FULL/MINIMAL/COMPACT) so that each platform and model
combination gets optimized output from a single source of truth.
"""

from __future__ import annotations

from typing import Literal

import structlog

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._templates import (
    CEREMONY_TOOLS,
    PHASE_DESCRIPTIONS,
)

_logger = structlog.get_logger(__name__)

# Type alias for the ceremony mode literal
CeremonyMode = Literal["FULL", "MINIMAL", "COMPACT"]

# Gemini marker constants (shared with _gemini.py)
_GEMINI_TRW_START_MARKER = "<!-- trw:gemini:start -->"
_GEMINI_TRW_END_MARKER = "<!-- trw:gemini:end -->"

# Canonical session-boundary text — import from here, not _static_sections.
SESSION_BOUNDARY_TEXT = (
    "Every session that loads learnings via `trw_session_start()` should persist "
    "them at session end \u2014 this is how your work compounds across sessions "
    "instead of being lost.\n"
)

# Quick-ref subset: the 4 highest-signal tools shown in the compact CLAUDE.md table
_QUICK_REF_TOOLS = ("trw_session_start", "trw_learn", "trw_checkpoint", "trw_deliver")
_QUICK_REF_SIGNATURES = {
    "trw_session_start": "trw_session_start()",
    "trw_learn": "trw_learn(summary, detail)",
    "trw_checkpoint": "trw_checkpoint(message)",
    "trw_deliver": "trw_deliver()",
}


class ProtocolRenderer:
    """Centralizes protocol formatting for different AI coding assistants.

    PRD-CORE-131-FR01: Intake ``ClientProfile`` + ``ModelFamily`` and produce
    standard TRW Markdown sections. All generators delegate here.

    PRD-CORE-131-FR04: ``ceremony_mode`` drives verbosity (FULL/MINIMAL/COMPACT).
    """

    def __init__(
        self,
        client_profile: ClientProfile | None = None,
        model_family: str = "generic",
        ceremony_mode: CeremonyMode = "FULL",
        # Legacy compat for _opencode_sections.py which passes platform= directly
        platform: str | None = None,
    ) -> None:
        if client_profile is not None:
            self.client_profile = client_profile
            self.platform = client_profile.client_id
        else:
            self.client_profile = ClientProfile(
                client_id=platform or "generic",
                display_name=platform or "generic",
            )
            self.platform = platform or "generic"
        self.model_family = model_family
        self.ceremony_mode: CeremonyMode = ceremony_mode
        _logger.debug(
            "renderer_init",
            platform=self.platform,
            model_family=model_family,
            ceremony_mode=ceremony_mode,
        )

    # ------------------------------------------------------------------
    # FR02: Ceremony quick-reference table (from CEREMONY_TOOLS)
    # ------------------------------------------------------------------

    def render_ceremony_quick_ref(self) -> str:
        """Render compact ceremony quick-reference card for CLAUDE.md.

        PRD-CORE-131-FR02: Generated from ``CEREMONY_TOOLS`` with
        client-specific notes injection (e.g., Gemini 1M token advice).
        Only the 4 highest-signal tools are shown in the compact table;
        the full table is in ``render_ceremony_table()``.
        """
        lines = [
            "## TRW Behavioral Protocol (Auto-Generated)",
            "",
            "| Tool | When | Why |",
            "|------|------|-----|",
        ]
        for ct in CEREMONY_TOOLS:
            if ct.tool in _QUICK_REF_TOOLS:
                signature = _QUICK_REF_SIGNATURES.get(ct.tool, ct.example)
                tool_cell = f"`{signature}`"
                if ct.example != signature:
                    tool_cell = f"{tool_cell}<br><sub>e.g. `{ct.example}`</sub>"
                lines.append(f"| {tool_cell} | {ct.when} | {ct.what} |")
        lines.extend(["", "Full tool lifecycle: `/trw-ceremony-guide`", ""])
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Phase descriptions
    # ------------------------------------------------------------------

    def render_phase_descriptions(self) -> str:
        """Render phase arrow diagram and description list."""
        phase_names = [p[0] for p in PHASE_DESCRIPTIONS]
        lines = [
            "### Execution Phases",
            "",
            "```",
            " \u2192 ".join(phase_names),
            "```",
            "",
        ]
        lines.extend(f"- **{name}**: {purpose}" for name, purpose in PHASE_DESCRIPTIONS)
        lines.append("")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # FR02: Full ceremony tools table (from CEREMONY_TOOLS)
    # ------------------------------------------------------------------

    def render_ceremony_table(self) -> str:
        """Render ceremony tools as a full markdown table.

        PRD-CORE-131-FR02: All rows generated from ``CEREMONY_TOOLS``.
        """
        lines = [
            "### Tool Lifecycle",
            "",
            "| Phase | Tool | When to Use | What It Does | Example |",
            "|-------|------|-------------|--------------|---------|",
        ]
        lines.extend(f"| {ct.phase} | `{ct.tool}` | {ct.when} | {ct.what} | `{ct.example}` |" for ct in CEREMONY_TOOLS)
        lines.append("")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Ceremony flows
    # ------------------------------------------------------------------

    def render_ceremony_flows(self) -> str:
        """Render quick task and full run example flows."""
        return (
            "### Example Flows\n"
            "\n"
            "**Quick Task** (no run needed):\n"
            "```\n"
            "trw_session_start -> work -> trw_learn (if discovery) -> trw_deliver()\n"
            "```\n"
            "\n"
            "**Full Run**:\n"
            "```\n"
            "trw_session_start -> trw_init(task_name, prd_scope)\n"
            "  -> work + trw_checkpoint (periodic) + trw_learn (discoveries)\n"
            "  -> trw_build_check(scope='full')           [VALIDATE]\n"
            "  -> review diff, fix gaps, trw_learn         [REVIEW]\n"
            "  -> trw_deliver()\n"
            "```\n"
            "\n"
        )

    # ------------------------------------------------------------------
    # Framework reference
    # ------------------------------------------------------------------

    def render_framework_reference(self) -> str:
        """Render framework reference directive.

        Gated by ``include_framework_ref`` on client profile.
        """
        if not self.client_profile.include_framework_ref:
            return ""

        return (
            "### Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines "
            "phase gates, exit criteria, quality rubrics, and formation selection. "
            "Re-read after context compaction.\n"
            "\n"
        )

    # ------------------------------------------------------------------
    # Closing reminder
    # ------------------------------------------------------------------

    def render_closing_reminder(self) -> str:
        """Render closing reminder that bookends the auto-generated section."""
        return "### Session Boundaries\n\n" + SESSION_BOUNDARY_TEXT + "\n"

    # ------------------------------------------------------------------
    # FR04: Behavioral protocol (FULL mode)
    # ------------------------------------------------------------------

    def render_behavioral_protocol(self) -> str:
        """Generate the full behavioral protocol as markdown.

        PRD-CORE-131-FR04: FULL ceremony mode output.
        """
        parts: list[str] = [
            "# TRW Behavioral Protocol\n",
            self.render_ceremony_quick_ref(),
            self.render_phase_descriptions(),
            self.render_ceremony_table(),
            self.render_ceremony_flows(),
            self.render_framework_reference(),
            self.render_closing_reminder(),
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # FR04: Minimal protocol (MINIMAL mode)
    # ------------------------------------------------------------------

    def render_minimal_protocol(self) -> str:
        """Render a shortened ceremony protocol for local model AGENTS.md.

        PRD-CORE-131-FR04: MINIMAL ceremony mode output.
        Must be under 200 tokens.
        """
        return (
            "TRW tools persist your work across sessions:\n"
            "- **Start**: call `trw_session_start()` to load prior learnings\n"
            "- **Finish**: call `trw_deliver()` to persist discoveries (not status reports)\n"
            "- **Verify**: Run tests after each change \u2014 fix failures before moving on.\n"
            "\n" + SESSION_BOUNDARY_TEXT
        )

    # ------------------------------------------------------------------
    # FR04: Compact protocol (COMPACT mode)
    # ------------------------------------------------------------------

    def render_compact_protocol(self) -> str:
        """Render a compact ceremony protocol — quick-ref table + session boundaries.

        PRD-CORE-131-FR04: COMPACT ceremony mode output.
        Includes the quick-reference table (4 core tools) and session
        boundary reminder, but omits phases, full tool table, and flows.
        Suitable for sub-instruction files and smaller context windows.
        """
        return self.render_ceremony_quick_ref() + SESSION_BOUNDARY_TEXT

    # ------------------------------------------------------------------
    # Gemini instructions
    # ------------------------------------------------------------------

    def render_gemini_instructions(self) -> str:
        """Generate GEMINI.md TRW ceremony section.

        PRD-CORE-149-FR10: body extracted to renderers/_review_and_opencode.py.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_gemini_instructions

        return render_gemini_instructions()

    # FR03: OpenCode model-specific instructions
    # ------------------------------------------------------------------

    def render_opencode_instructions(self) -> str:
        """Render instructions for OpenCode .opencode/INSTRUCTIONS.md.

        PRD-CORE-131-FR03: Model-specific reasoning injections based on
        ``model_family`` (Qwen /think, Claude extended thinking, GPT CoT).
        """
        family = self.model_family
        if family == "qwen":
            return self._render_opencode_qwen()
        if family == "gpt":
            return self._render_opencode_gpt()
        if family == "claude":
            return self._render_opencode_claude()
        return self._render_opencode_generic()

    def _render_opencode_qwen(self) -> str:
        """Render OpenCode instructions optimised for Qwen (local vLLM).

        PRD-CORE-149-FR10: body extracted to renderers/_review_and_opencode.py.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_opencode_qwen

        return render_opencode_qwen()

    def _render_opencode_gpt(self) -> str:
        """Render OpenCode instructions optimised for GPT models.

        PRD-CORE-149-FR10: body extracted to renderers/_review_and_opencode.py.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_opencode_gpt

        return render_opencode_gpt()

    def _render_opencode_claude(self) -> str:
        """Render OpenCode instructions optimised for Claude (Anthropic API).

        PRD-CORE-149-FR10: body extracted to renderers/_review_and_opencode.py.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_opencode_claude

        return render_opencode_claude()

    def _render_opencode_generic(self) -> str:
        """Render OpenCode instructions for unknown/generic models.

        PRD-CORE-149-FR10: body extracted to renderers/_review_and_opencode.py.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_opencode_generic

        return render_opencode_generic()
