"""Static CLAUDE.md section renderers — protocol, ceremony, delegation, watchlist."""

from __future__ import annotations

from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.claude_md._templates import (
    BEHAVIORAL_PROTOCOL_CAP,
    CEREMONY_TOOLS,
    PHASE_DESCRIPTIONS,
)


def render_imperative_opener() -> str:
    """Render the value-oriented opener for the TRW auto-generated section.

    Appears at the very top of the auto-generated block. Uses motivation
    framing (what the tools give you) rather than threat framing (what
    you lose). Research: Anthropic context engineering, Claude 4.6 best
    practices recommend natural language over CRITICAL/ALWAYS/NEVER.

    Returns:
        Markdown string with value-framed instructions.
    """
    return (
        "Your primary role is **orchestration** — you produce better outcomes by assessing tasks, "
        "delegating to focused agents (subagents or Agent Teams), verifying integration, and "
        "preserving knowledge. Reserve direct implementation for trivial edits "
        "(\u22643 lines, 1 file). For everything else, delegate.\n"
        "\n"
        "TRW tools help you build effectively and preserve your work across sessions:\n"
        "- **Start**: call `trw_session_start()` to load prior learnings"
        " and recover any active run\n"
        "- **Start**: read `.trw/frameworks/FRAMEWORK.md` \u2014 it defines the methodology"
        " your tools implement\n"
        "- **During**: call `trw_checkpoint(message)` after milestones"
        " so you resume here if context compacts\n"
        "- **Finish**: call `trw_deliver()` to persist your learnings"
        " for future sessions\n"
        "\n"
    )


def render_ceremony_quick_ref() -> str:
    """Render compact ceremony quick-reference card for CLAUDE.md.

    Lists only the 4 ceremony-critical tools with a pointer to the
    full ceremony guide skill for on-demand loading.

    Returns:
        Markdown string with quick-reference card.
    """
    return (
        "## TRW Behavioral Protocol (Auto-Generated)\n"
        "\n"
        "- `trw_session_start()` \u2014 loads your prior learnings and recovers any active run\n"
        "- `trw_checkpoint(message)` \u2014 saves progress so you can resume after context compaction\n"
        "- `trw_learn(summary, detail)` \u2014 records discoveries for all future sessions\n"
        "- `trw_deliver()` \u2014 persists everything in one call when done\n"
        "\n"
        "For full tool guide: invoke `/trw-ceremony-guide`\n"
        "\n"
        "Sessions where you orchestrate (delegate, verify, learn) "
        "rather than implement directly produce higher quality and "
        "fewer rework cycles \u2014 your strategic oversight is more "
        "valuable than your keystrokes.\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml.

    Returns:
        Markdown bullet list of directives, or empty string if file missing.
    """
    import trw_mcp.state.claude_md as _pkg

    from trw_mcp.exceptions import StateError

    proto_path = (
        resolve_project_root() / _pkg._config.trw_dir / _pkg._config.context_dir / "behavioral_protocol.yaml"
    )
    if not proto_path.exists():
        return ""
    try:
        data = _pkg._reader.read_yaml(proto_path)
    except (StateError, ValueError, TypeError):
        return ""
    directives = data.get("directives", [])
    if not directives or not isinstance(directives, list):
        return ""
    lines = [f"- {d}" for d in directives[:BEHAVIORAL_PROTOCOL_CAP]]
    lines.append("")
    return "\n".join(lines) + "\n"


def render_phase_descriptions() -> str:
    """Render phase arrow diagram and description list.

    Returns:
        Markdown string with phase flow and descriptions.
    """
    phase_names = [p[0] for p in PHASE_DESCRIPTIONS]
    lines: list[str] = [
        "### Execution Phases",
        "",
        "```",
        " \u2192 ".join(phase_names),
        "```",
        "",
    ]
    for name, purpose in PHASE_DESCRIPTIONS:
        lines.append(f"- **{name}**: {purpose}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_ceremony_table() -> str:
    """Render ceremony tools as a markdown table.

    Returns:
        Markdown table with Phase, Tool, When, What, Example columns.
    """
    lines: list[str] = [
        "### Tool Lifecycle",
        "",
        "| Phase | Tool | When to Use | What It Does | Example |",
        "|-------|------|-------------|--------------|---------|",
    ]
    for ct in CEREMONY_TOOLS:
        lines.append(
            f"| {ct.phase} | `{ct.tool}` | {ct.when} | {ct.what} | `{ct.example}` |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def render_ceremony_flows() -> str:
    """Render quick task and full run example flows.

    Returns:
        Markdown string with two flow diagrams.
    """
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


def render_delegation_protocol() -> str:
    """Render delegation discipline section for CLAUDE.md auto-generation.

    Provides a compact delegation decision tree and mode comparison so
    agents default to delegation for non-trivial tasks. Uses value-oriented
    framing (why delegation produces better results) rather than prescriptive
    mandates (MUST/NEVER).

    Returns:
        Markdown string with delegation guidance.
    """
    return (
        "## TRW Delegation & Orchestration (Auto-Generated)\n"
        "\n"
        "As orchestrator, your responsibilities are: (1) assess and decompose tasks, "
        "(2) delegate to focused agents, (3) verify integration and quality, "
        "(4) maintain strategic oversight, (5) preserve knowledge via TRW tools. "
        "Direct implementation is reserved for trivial edits only.\n"
        "\n"
        "### When to Delegate\n"
        "\n"
        "```\n"
        "Task arrives \u2192 Assess scope\n"
        "\u251c\u2500\u2500 Trivial? (\u22643 lines, 1 file) \u2192 Self-implement\n"
        "\u251c\u2500\u2500 Research/read-only?          \u2192 Subagent (Explore/Plan type)\n"
        "\u251c\u2500\u2500 Single-scope? (\u22643 files)     \u2192 Subagent (general-purpose)\n"
        "\u251c\u2500\u2500 Multi-scope? (4+ files)\n"
        "\u2502   \u251c\u2500\u2500 Independent tracks?      \u2192 Batched subagents\n"
        "\u2502   \u2514\u2500\u2500 Interdependent?          \u2192 Agent Team\n"
        "\u2514\u2500\u2500 Sprint-scale? (4+ PRDs)      \u2192 Agent Team + playbooks\n"
        "```\n"
        "\n"
        "**Default: subagents.** Use Agent Teams when teammates need peer communication "
        "or when tasks span 2+ modules with shared interfaces. As team lead, you "
        "orchestrate, monitor, and validate \u2014 teammates do the implementation.\n"
        "\n"
    )


def render_rationalization_watchlist() -> str:
    """Render anti-rationalization watchlist and rigid/flexible classification.

    Lists specific thoughts agents have when skipping process, paired with
    consequence-framed counter-arguments. Research basis: superpowers framework
    (obra/superpowers), Cialdini persuasion principles (Meincke et al. 2025),
    consequence framing (BCSP Neurocomputing 2025).

    Returns:
        Markdown string with watchlist table and tool classification.
    """
    return (
        "## Rationalization Watchlist (Auto-Generated)\n"
        "\n"
        "If you catch yourself thinking any of these, stop and follow the process:\n"
        "\n"
        "| Thought | Why it's wrong | Consequence |\n"
        "|---------|---------------|-------------|\n"
        '| "This is too simple for ceremony" '
        "| Simple tasks compound into gaps when 10 agents skip in parallel "
        "| You skip checkpoint \u2192 context compacts \u2192 you re-implement from scratch |\n"
        '| "I\'ll checkpoint/deliver after I finish this part" '
        "| Context compaction erases uncheckpointed work permanently "
        "| Past agents who skipped trw_deliver lost all session learnings |\n"
        '| "I already know the codebase" '
        "| Prior learnings contain gotchas for exactly this area "
        "| Sprint 26 had 6 P0/P1 defects from agents who skipped recall |\n"
        '| "I can implement directly, delegation is overhead" '
        "| Subagent implementation has 3x fewer P0 defects "
        "| Your focused context is valuable \u2014 subagents get deeper context per task |\n"
        '| "The build check can wait until the end" '
        "| Late build failures cascade into multi-file rework "
        "| 2x rework when caught at DELIVER vs catching at VALIDATE |\n"
        "\n"
        "### Rigid Tools (never skip, unconditional)\n"
        "\n"
        "- `trw_session_start()` \u2014 always, first action\n"
        "- `trw_deliver()` \u2014 always, last action\n"
        "- `trw_build_check()` \u2014 always at VALIDATE and DELIVER\n"
        "- Completion artifacts \u2014 always before marking task complete\n"
        "\n"
        "### Flexible Tools (must happen, you pick timing)\n"
        "\n"
        "- `trw_checkpoint()` \u2014 at milestones (you judge which)\n"
        "- `trw_learn()` \u2014 on discoveries/gotchas/errors\n"
        "- `trw_recall()` \u2014 recommended at start, skippable for repeat-domain\n"
        "\n"
    )


def render_framework_reference() -> str:
    """Render framework reference directive for CLAUDE.md.

    Points agents to the methodology document and explains *why* reading it
    matters. Kept compact (~6 lines) per PRD-CORE-061 progressive disclosure.

    The framework (.trw/frameworks/FRAMEWORK.md) defines the methodology that
    TRW tools implement. Without it, agents use tools correctly but miss the
    process — phase gates, exit criteria, formations, quality rubrics — that
    prevents rework. This section ensures agents know the framework exists and
    understand why investing ~500 tokens to read it at session start pays for
    itself in avoided rework.

    Returns:
        Markdown string with framework reference and reading schedule.
    """
    return (
        "### Framework Reference\n"
        "\n"
        "**Read `.trw/frameworks/FRAMEWORK.md` at session start** — it defines the methodology "
        "your tools implement.\n"
        "\n"
        "The framework covers: 6-phase execution model with exit criteria per phase, formation "
        "selection for parallel work, quality gates with rubric scoring, phase reversion rules, "
        "adaptive planning, anti-skip safeguards, and Agent Teams protocol. "
        "Re-read after context compaction and at phase transitions. "
        "Without it, tools work but methodology is missing — you'll pass tool checks while "
        "skipping the process that prevents rework.\n"
        "\n"
    )


def render_closing_reminder() -> str:
    """Render closing reminder that bookends the auto-generated section.

    Position bias research (Liu et al. 2024) shows the end of a prompt
    gets elevated attention weight. This repeats the two most-skipped
    ceremony tools in a different semantic frame from the imperative opener.

    Returns:
        Markdown string with closing reminder.
    """
    return (
        "### Session Boundaries\n"
        "\n"
        "Every session that loads learnings via `trw_session_start()` "
        "should persist them at session end \u2014 this is how "
        "your work compounds across sessions instead of being lost.\n"
        "\n"
    )


def render_agent_teams_protocol() -> str:
    """Render Agent Teams protocol section for CLAUDE.md auto-generation.

    Provides teammates with dual-mode orchestration guidance, lifecycle
    expectations, and hook-based quality gates (PRD-INFRA-010).

    Returns:
        Markdown string with Agent Teams protocol, or empty string
        if the feature is not enabled.
    """
    import trw_mcp.state.claude_md as _pkg

    if not _pkg._config.agent_teams_enabled:
        return ""

    return (
        "## TRW Agent Teams Protocol (Auto-Generated)\n"
        "\n"
        "### Dual-Mode Orchestration\n"
        "\n"
        "| Mode | When | How |\n"
        "|------|------|-----|\n"
        "| Subagents | Focused tasks, research, cost-sensitive | `Task` tool with `subagent_type` |\n"
        "| Agent Teams | Complex multi-file, peer coordination | `TeamCreate` + `Task` with `team_name` |\n"
        "\n"
        "### Teammate Lifecycle\n"
        "\n"
        "1. LEAD calls `TeamCreate` and `TaskCreate` for work items\n"
        "2. LEAD spawns teammates via `Task` tool with `team_name` parameter\n"
        "3. Teammates claim tasks via `TaskUpdate` (set `owner`)\n"
        "4. Teammates work autonomously, using `trw_learn`/`trw_checkpoint` for ceremony\n"
        "5. Teammates mark tasks `completed` via `TaskUpdate` when done\n"
        "6. LEAD sends `shutdown_request` when all tasks complete\n"
        "\n"
        "### Quality Gate Hooks\n"
        "\n"
        "- **TeammateIdle**: Fires when teammate goes idle \u2014 soft gate, logs for monitoring\n"
        "- **TaskCompleted**: Fires when task marked complete \u2014 extension point for validation\n"
        "\n"
        "### File Ownership\n"
        "\n"
        "Each teammate owns exclusive files to prevent write conflicts. "
        "LEAD assigns ownership via playbook. Never edit files outside your assignment.\n"
        "\n"
        # Adding a new agent? See TestAgentDefinitions in test_agent_teams.py
        # for the full 7-location update sequence.
        "### Teammate Roles\n"
        "\n"
        "| Agent | Model | Purpose |\n"
        "|-------|-------|---------|\n"
        "| `trw-lead` | opus | Team lead, 6-phase orchestrator, quality gates |\n"
        "| `trw-implementer` | sonnet | Code implementation, TDD |\n"
        "| `trw-tester` | sonnet | Test coverage, edge cases |\n"
        "| `trw-reviewer` | opus | Code review, security audit |\n"
        "| `trw-researcher` | sonnet | Codebase research, docs |\n"
        "\n"
    )
