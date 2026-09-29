"""Antigravity instruction renderer (``.agents/rules/trw-ceremony.md`` and ``ANTIGRAVITY.md``).

The opencode renderers that shared this module were deleted by PRD-CORE-301-FR02;
opencode now renders the shared claude-code block with opencode framing.
"""

from __future__ import annotations

from trw_mcp.models.config._pre_edit_channels import render_pre_edit_hint_instruction

# Shared Antigravity marker constants.
_ANTIGRAVITY_TRW_START_MARKER = "<!-- trw:antigravity:start -->"
_ANTIGRAVITY_TRW_END_MARKER = "<!-- trw:antigravity:end -->"


def _antigravity_delegation_block() -> str:
    """Return the delegation block iff antigravity-cli's profile enables it.

    PRD-CORE-252 OQ-3 wiring-defect fix (2026-09-04): antigravity-cli's
    ``include_delegation`` has been True since the profile was added, but
    this renderer never called ``render_delegation_protocol()`` — the flag
    had no consumer. Function-local imports avoid a module-import cycle with
    ``sections`` (established pattern in this package).
    """
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md.sections._delegation import render_delegation_protocol

    return render_delegation_protocol(resolve_client_profile("antigravity-cli"))


#: Client id whose surfaces this module's Antigravity renderer describes. Every
#: fact about that client below is looked up from its own registry entry, never
#: restated here.
_ANTIGRAVITY_CLIENT = "antigravity-cli"


def _bundled_agent_stems() -> list[str]:
    """Stems of the bundled agent corpus — the set every install materializes.

    Derived from ``data/agents/*.md``, which is the same directory
    ``_install_agents`` iterates, so the rendered list cannot name an agent the
    install does not place. The Antigravity block used to hardcode four names,
    one of which (``trw-explorer``) was retired with the pre-PRD-CORE-252
    templates and shipped to no client at all.

    Entitlement-gated additions (the trw-distill explorer subagent) are
    deliberately excluded: they are installed by a separate, conditional
    channel, so promising them unconditionally would reintroduce the same defect
    from the other direction.
    """
    from importlib.resources import files as _pkg_files

    agents_dir = _pkg_files("trw_mcp.data").joinpath("agents")
    return sorted(p.name.removesuffix(".md") for p in agents_dir.iterdir() if p.name.endswith(".md"))


def _antigravity_subagent_block() -> str:
    """Render the Subagents section from the agent-format registry.

    The destination is read from
    :func:`trw_mcp.agents.agent_formats.agent_format_for`, which is the single
    source the installer writes to. The hardcoded ``.antigravitycli/agents/``
    in this block survived PRD-CORE-252's move to ``.agents/agents`` and pointed
    every Antigravity agent at a directory the install never creates.
    """
    from trw_mcp.agents.agent_formats import agent_format_for

    fmt = agent_format_for(_ANTIGRAVITY_CLIENT)
    if not fmt.supports_agents or fmt.destination_dir is None:
        return f"### Subagents\n\nTRW installs no subagents for this client: {fmt.unsupported_reason}\n"

    stems = _bundled_agent_stems()
    names = ", ".join(f"`@{stem}`" for stem in stems)
    return (
        "### Subagents\n"
        "\n"
        f"TRW installs the bundled specialists into `{fmt.destination_dir}/` "
        f"(one `{fmt.filename_suffix}` file per agent). Reference one by name:\n"
        "\n"
        f"{names}\n"
    )


def _antigravity_tool_reference() -> str:
    """Render the MCP-tool naming section from the profile's own namespace.

    ``antigravity-cli``'s ``ClientProfile.tool_namespace_prefix`` is empty, so
    tools are exposed under their bare names. This block asserted an
    ``mcp_trw_`` prefix — the identical drift
    ``agents/agent_formats.py`` was built to remove from the Antigravity agent
    templates, left in place in the instruction carrier that tells the same
    agents how to call a tool.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.prompts.messaging import render_tool_name

    profile = resolve_client_profile(_ANTIGRAVITY_CLIENT)
    start = render_tool_name("trw_session_start", profile)
    learn = render_tool_name("trw_learn", profile)
    naming = (
        f"All TRW tools are exposed via MCP under the `{profile.tool_namespace_prefix}` prefix."
        if profile.tool_namespace_prefix
        else "All TRW tools are exposed via MCP under their own names (no prefix)."
    )
    keys = ", ".join(
        f"`{render_tool_name(name, profile)}`"
        for name in (
            "trw_session_start",
            "trw_learn",
            "trw_checkpoint",
            "trw_deliver",
            "trw_init",
            "trw_status",
            "trw_recall",
            "trw_build_check",
            "trw_review",
            "trw_prd_validate",
        )
    )
    return (
        "### MCP Tools\n"
        "\n"
        f"{naming}\n"
        f"Call `{start}` first in every session.\n"
        "\n"
        f"Key tools: {keys}.\n"
        "\n"
        "### Memory Routing\n"
        "\n"
        f"- Code patterns, gotchas, build tricks → `{learn}()`\n"
        "- User preferences → antigravity-cli's built-in memory if applicable\n"
    )


def render_antigravity_instructions() -> str:
    """Render ANTIGRAVITY.md TRW ceremony section (deliver gate from the shared single source, TB-17)."""
    delegation = _antigravity_delegation_block()
    subagents = _antigravity_subagent_block()
    tool_reference = _antigravity_tool_reference()
    deliver_gate = _deliver_gate_block()
    pre_edit_hint = render_pre_edit_hint_instruction()  # PRD-CORE-336-FR04 fallback line
    return f"""{_ANTIGRAVITY_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

## TRW Framework Integration

This project uses the [TRW Framework](https://trwframework.com) for structured
AI-assisted development. TRW gives your antigravity-cli sessions persistent engineering
memory — patterns, gotchas, and project knowledge accumulate across sessions.

### Session Protocol

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings |
| `trw_learn(summary, detail)` | On errors, discoveries, or gotchas | Saves a non-obvious pattern or mistake so no future agent repeats it — this is how institutional knowledge grows. Routine status ("task completed", "PRD groomed") is not a learning; a session that genuinely produced none is a valid result. |
| `trw_checkpoint(message)` | After milestones | Resume point if context compacts |
| `trw_deliver()` | Completed-work acceptance | Existing delivery gates apply; recorded learnings already persist. |

Preserve material unfinished work with a checkpoint or durable native handoff and a next-read pointer. Nothing material to preserve: do not manufacture artifacts. Use trw_deliver only for completed-work acceptance under unchanged delivery gates; recorded learnings already persist.

{deliver_gate}

{tool_reference}
{subagents}
### Conventions

- Run the project-native validation command after each meaningful change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)
{pre_edit_hint}

{delegation}
{_ANTIGRAVITY_TRW_END_MARKER}
"""


def _deliver_gate_block() -> str:
    """Return the FR03 non-negotiable session-start + deliver-gate block.

    Function-local import of the bundled-source loader (PRD-QUAL-104 FR02/FR03)
    avoids an import cycle with the ``sections`` package, which transitively
    imports the renderers.
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    return render_deliver_gate_statement()
