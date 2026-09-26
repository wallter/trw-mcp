"""OpenCode instruction renderers.

The public wrapper names are kept for compatibility, but v25 emits a
portable instruction body instead of model-family-specific prompt text.
"""

from __future__ import annotations

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
    """Render ANTIGRAVITY.md TRW ceremony section."""
    delegation = _antigravity_delegation_block()
    subagents = _antigravity_subagent_block()
    tool_reference = _antigravity_tool_reference()
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

### Deliver Gate

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` reported `tests_passed=true` and `static_checks_clean=true` (or omitted), with a non-zero `test_count` and a non-empty `scope`, **or**
- (b) `allow_unverified=true` and `unverified_reason` contains a valid, unexpired
  acceptable-failure record with `failed_command`, `residual_risk`, `owner`, and
  `expiry_iso`, **or**
- (c) an authorized operator/config override is recorded with technical rationale.

A review-verdict label or free-text reason alone is not an acceptable-failure record.
Under the default `block_coding` mode a missing build check blocks when the task type
expects a build artifact (`coding`, `rca`, `eval`) OR when the session modified files —
whatever the task type. A run that changed nothing stays advisory.

{tool_reference}
{subagents}
### Conventions

- Run the project-native validation command after each meaningful change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

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


def _render_opencode_portable() -> str:
    """Render OpenCode instructions without model/provider assumptions.

    PRD-QUAL-104 FR03: appends the non-negotiable session-start + deliver-gate
    block (bundled-source derived) so the OpenCode protocol carrier always
    states the gate verbatim.
    """
    return _render_opencode_portable_body() + "\n" + _deliver_gate_block()


def _render_opencode_portable_body() -> str:
    """Render the portable OpenCode instruction body (pre-FR03 content)."""
    return (
        "# TRW Instructions\n"
        "\n"
        "## Model and Context Policy\n"
        "\n"
        "Assume capabilities vary. Keep prompts concise, reference files by path, "
        "and discover the active model/context limits from the harness before relying "
        "on large-context behavior. Treat family-specific prompting tricks as optional "
        "adapter knowledge, not core TRW requirements.\n"
        "\n"
        "## Workflow\n"
        "\n"
        "1. **Start**: call `trw_session_start()` — loads prior learnings and active state\n"
        "2. **Scope**: identify the governing request/PRD, files, language/toolchain, and verification path\n"
        "3. **Implement**: keep changes bounded; use focused helpers only when available\n"
        "4. **Verify**: run targeted project-native checks and fix failures immediately\n"
        "5. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)\n"
        "6. Preserve material unfinished work via checkpoint or durable native handoff with next-read pointer; nothing material to preserve: no artifact needed. Use `trw_deliver()` for completed-work acceptance under the delivery gates\n"
        "\n"
        "## Ceremony Protocol\n"
        "\n"
        "- `trw_session_start()` — **first action** in every session\n"
        "- `trw_checkpoint(message)` — save progress at meaningful milestones\n"
        "- `trw_recall(query)` — retrieve relevant prior learnings before re-solving\n"
        "- `trw_learn(summary, detail)` — record durable gotchas/patterns, not routine status\n"
        "- `trw_build_check()` — record project-native validation before delivery after code changes\n"
        "- `trw_deliver()` — completed-work acceptance under unchanged delivery gates\n"
        "\n"
        "## Nudge Policy\n"
        "\n"
        "Nudges are short, evidence-grounded reminders surfaced by TRW tools or adapters. "
        "Follow them when they identify a real missing step, but do not treat a nudge "
        "as validation evidence. Respect density, budget, and cooldown settings.\n"
        "\n"
        "## Portable Prompting Patterns\n"
        "\n"
        "- Prefer small, schema-shaped requests with explicit file paths and acceptance criteria\n"
        "- Ask helpers for changed paths, validation run, and residual risks\n"
        "- Use sequential tool calls if the harness has parser or parallel-call limits\n"
        "- Do not paste large files inline unless the harness has proven budget headroom\n"
        "- If a model-specific adapter recommends special syntax, verify it in that adapter first\n"
        "\n"
        "## Known Limitations\n"
        "\n"
        "- **Unknown context budget**: default to bounded reads and checkpoints\n"
        "- **Harness variance**: hooks, skills, and helper agents may be absent or advisory\n"
        "- **Tool-call variance**: validate arguments and recover with `trw_session_start()` after restarts\n"
        "\n"
        "## Framework Reference\n"
        "\n"
        "Read `.trw/frameworks/FRAMEWORK.md` at session start for the phase gates, "
        "quality rubric, model policy, and delivery rules that the tools implement.\n"
    )


def render_opencode_qwen() -> str:
    """Compatibility wrapper; v25 emits portable OpenCode instructions."""
    return _render_opencode_portable()


def render_opencode_gpt() -> str:
    """Compatibility wrapper; v25 emits portable OpenCode instructions."""
    return _render_opencode_portable()


def render_opencode_claude() -> str:
    """Compatibility wrapper; v25 emits portable OpenCode instructions."""
    return _render_opencode_portable()


def render_opencode_generic() -> str:
    """Render OpenCode instructions for unknown/generic models."""
    return _render_opencode_portable()
