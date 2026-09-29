"""Unified renderer for TRW protocol instructions.

PRD-CORE-131: Centralizes all ceremony guidance generation into a single
``ProtocolRenderer`` class, replacing hardcoded strings scattered across
``_static_sections.py``.

The renderer is parameterized by ``ClientProfile``, an optional legacy
``model_family`` hint, and ``ceremony_mode`` (FULL/MINIMAL/COMPACT). v25 keeps
model-family routing as a compatibility shim while emitting portable guidance by
default.
"""

from __future__ import annotations

from typing import Literal

import structlog

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._catalogue import CEREMONY_POINTER, catalogue_is_verbatim
from trw_mcp.state.claude_md._templates import (
    CEREMONY_TOOLS,
    PHASE_DESCRIPTIONS,
)

_logger = structlog.get_logger(__name__)

# Type alias for the ceremony mode literal
CeremonyMode = Literal["FULL", "MINIMAL", "COMPACT"]

# Canonical session-boundary text — import from here, not _static_sections.
SESSION_BOUNDARY_TEXT = (
    "Preserve material unfinished work via checkpoint or durable native handoff + next-read pointer. "
    "Nothing material to preserve: no artifacts. trw_deliver is completed-work acceptance under unchanged gates; "
    "recorded learnings already persist.\n"
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

    PRD-CORE-131-FR01: Intake ``ClientProfile`` plus an optional legacy
    model-family hint and produce standard TRW Markdown sections. All generators
    delegate here.

    PRD-CORE-131-FR04: ``ceremony_mode`` drives verbosity (FULL/MINIMAL/COMPACT).
    """

    def __init__(
        self,
        client_profile: ClientProfile | None = None,
        model_family: str = "generic",
        ceremony_mode: CeremonyMode = "FULL",
        # Callers without a profile name the client by ``platform``.
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
        # PRD-CORE-149 NFR04: every renderer logs ``profile_rendering`` with
        # client_id, display_name, and ceremony_mode so operators can verify
        # which profile drove a given render.
        _logger.info(
            "profile_rendering",
            client_id=self.client_profile.client_id,
            display_name=self.client_profile.display_name,
            ceremony_mode=ceremony_mode,
        )

    # ------------------------------------------------------------------
    # FR02: Ceremony quick-reference table (from CEREMONY_TOOLS)
    # ------------------------------------------------------------------

    def render_ceremony_quick_ref(self) -> str:
        """Render compact ceremony quick-reference card for CLAUDE.md.

        PRD-CORE-131-FR02: Generated from ``CEREMONY_TOOLS`` with
        client-specific notes injection (e.g., large-context token advice).
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

    def _catalogue_section(self) -> str:
        """Return the catalogue in the form this profile should receive (FR08).

        The rule — and why a light-ceremony profile always keeps the verbatim
        table — lives in :mod:`trw_mcp.state.claude_md._catalogue`.
        """
        verbatim = catalogue_is_verbatim(self.client_profile.ceremony_mode)
        return self.render_ceremony_table() if verbatim else CEREMONY_POINTER

    # ------------------------------------------------------------------
    # Ceremony flows
    # ------------------------------------------------------------------

    def render_ceremony_flows(self) -> str:
        """Render quick task and full run example flows."""
        return (
            "### Completed-work Flows (delivery gates apply)\n"
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
            "  -> trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, "
            "static_checks_clean=<bool|null>, scope='<exact command>') [VALIDATE]\n"
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

    def render_delegation_section(self) -> str:
        """Delegation protocol; no-ops per ``include_delegation`` (PRD-CORE-252 OQ-3)."""
        from trw_mcp.state.claude_md.sections._delegation import render_delegation_protocol

        return render_delegation_protocol(self.client_profile)

    # ------------------------------------------------------------------
    # FR04: Behavioral protocol (FULL mode)
    # ------------------------------------------------------------------

    def render_behavioral_protocol(self) -> str:
        """Generate the full behavioral protocol as markdown.

        PRD-CORE-131-FR04: FULL ceremony mode output.
        """
        from trw_mcp.state.claude_md.sections._tool_lifecycle import render_closing_reminder

        parts: list[str] = [
            "# TRW Behavioral Protocol\n",
            self.render_ceremony_quick_ref(),
            self.render_phase_descriptions(),
            self._catalogue_section(),  # PRD-CORE-247-FR08: pointer for full, table for light
            self.render_ceremony_flows(),
            self.render_framework_reference(),
            self.render_delegation_section(),
            # PRD-CORE-247: the ONE closing reminder. A same-named method
            # shadowed it here and returned session boundaries only, so this
            # block reached bare harnesses carrying neither the deliver gate nor
            # the offline substitutes (session-start.sh cats it verbatim out of
            # .trw/context/behavioral_protocol.md). Duplicate deleted, not
            # reconciled. Function-local import: sections._tool_lifecycle imports
            # this module at module scope.
            render_closing_reminder(),
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # FR04: Minimal protocol (MINIMAL mode)
    # ------------------------------------------------------------------

    def render_minimal_protocol(self) -> str:
        """Render the shortened MINIMAL-mode protocol for a light-mode AGENTS.md (PRD-CORE-131-FR04)."""
        from trw_mcp.state.claude_md.renderers._minimal import render_minimal_protocol

        return render_minimal_protocol(self.client_profile, SESSION_BOUNDARY_TEXT)

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
    # Antigravity CLI instructions
    # ------------------------------------------------------------------

    def render_antigravity_instructions(self) -> str:
        """Generate ANTIGRAVITY.md TRW ceremony section."""
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_antigravity_instructions

        return render_antigravity_instructions()
