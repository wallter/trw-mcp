"""Static CLAUDE.md section renderers — protocol, ceremony, delegation, watchlist."""

from __future__ import annotations

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.claude_md._templates import (
    BEHAVIORAL_PROTOCOL_CAP,
    CEREMONY_TOOLS,
    PHASE_DESCRIPTIONS,
)
from trw_mcp.state.persistence import FileStateReader

_SESSION_BOUNDARY_TEXT = (
    "Every session that loads learnings via `trw_session_start()` should persist "
    "them at session end \u2014 this is how your work compounds across sessions "
    "instead of being lost.\n"
)


def render_imperative_opener() -> str:
    """Render the value-oriented opener for the TRW auto-generated section.

    This is the highest-signal text in CLAUDE.md \u2014 it loads on every message.
    Designed to make ``trw_session_start()`` the obvious first action by
    framing it as the gateway to accumulated team knowledge. The session-start
    hook then delivers the full operational briefing (phases, delegation,
    watchlist) so CLAUDE.md stays compact.

    Prompt engineering: Uses concrete benefit framing (what you gain) rather
    than threat framing (what you lose). Specific numbers ground the claims.
    The "call this first" pattern leverages primacy bias in instruction
    following \u2014 the first concrete action in instructions gets highest
    compliance.

    Returns:
        Markdown string with role framing and session_start trigger.
    """
    return (
        "Your primary role is **orchestration** \u2014 delegate to focused agents "
        "for better outcomes than direct implementation. Focused subagents produce "
        "3x fewer P0 defects because they get deeper context per task. Reserve "
        "self-implementation for trivial edits (\u22643 lines, 1 file).\n"
        "\n"
        "**Your first action in every session must be `trw_session_start()`.**\n"
        "\n"
        "This single call loads everything you need: prior learnings from "
        "hundreds of past sessions, any active run state you can resume, "
        "and the full operational protocol (delegation guidance, phase gates, "
        "quality rubrics). Without it, you start from zero \u2014 with it, you "
        "start from the team\u2019s accumulated experience.\n"
        "\n"
        "After `trw_session_start()`, save progress with `trw_checkpoint()` "
        "after milestones, and close with `trw_deliver()` so your discoveries "
        "persist for future agents.\n"
        "\n"
    )


def render_ceremony_quick_ref() -> str:
    """Render compact ceremony quick-reference card for CLAUDE.md.

    Table format for scannability. Each tool gets when + what in one row.
    No redundancy with the imperative opener (which names tools briefly
    but doesn't explain them). Pointer to /trw-ceremony-guide for the
    full lifecycle reference.

    Returns:
        Markdown string with quick-reference table.
    """
    return (
        "## TRW Behavioral Protocol (Auto-Generated)\n"
        "\n"
        "| Tool | When | Why |\n"
        "|------|------|-----|\n"
        "| `trw_session_start()` | First action | Loads prior learnings so you don't repeat solved problems or rediscover known gotchas |\n"
        "| `trw_learn(summary, detail)` | On discoveries | Saves your finding so no future agent repeats your mistake |\n"
        "| `trw_checkpoint(message)` | After milestones | If context compacts, you resume here instead of re-implementing from scratch |\n"
        "| `trw_deliver()` | Last action | Persists your session's discoveries for future agents \u2014 without it, your learnings die with your context window |\n"
        "\n"
        "Full tool lifecycle: `/trw-ceremony-guide`\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml.

    Returns:
        Markdown bullet list of directives, or empty string if file missing.
    """
    from trw_mcp.exceptions import StateError

    config = get_config()
    reader = FileStateReader()

    proto_path = resolve_project_root() / config.trw_dir / config.context_dir / "behavioral_protocol.yaml"
    if not proto_path.exists():
        return ""
    try:
        data = reader.read_yaml(proto_path)
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


def render_ceremony_table() -> str:
    """Render ceremony tools as a markdown table.

    Returns:
        Markdown table with Phase, Tool, When, What, Example columns.
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
        "### Rigid Tools (unconditional \u2014 the cost of skipping exceeds the cost of running)\n"
        "\n"
        "- `trw_session_start()` \u2014 first action; loads accumulated knowledge so you start from the team's experience, not zero\n"
        "- `trw_deliver()` \u2014 last action; without this, your session's discoveries are invisible to every future agent\n"
        "- `trw_build_check()` \u2014 at VALIDATE and before DELIVER; late-caught bugs cascade into 2x rework\n"
        "- Completion artifacts \u2014 before marking complete; false completion reports cause downstream work to build on a foundation that doesn't exist\n"
        "\n"
        "### Flexible Tools (must happen, you choose the moment)\n"
        "\n"
        "- `trw_checkpoint()` \u2014 at milestones; your last checkpoint is your resume point after context compaction\n"
        "- `trw_learn()` \u2014 on discoveries; every learning you skip forces a future agent to rediscover it\n"
        "- `trw_recall()` \u2014 at start; prior agents already found the gotchas for your current task\n"
        "\n"
    )


def render_framework_reference() -> str:
    """Render framework reference directive for CLAUDE.md.

    Points agents to the methodology document. Compact per PRD-CORE-061
    progressive disclosure \u2014 the framework itself explains why, this
    section just says where and when to read it.

    Returns:
        Markdown string with framework pointer and reading schedule.
    """
    return (
        "### Framework Reference\n"
        "\n"
        "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines "
        "phase gates, exit criteria, quality rubrics, and formation selection. "
        "Re-read after context compaction.\n"
        "\n"
    )


def render_memory_harmonization() -> str:
    """Render memory-system routing guidance for Claude Code CLAUDE.md.

    Claude Code has native auto-memory (~/.claude/projects/.../memory/) that
    overlaps with TRW's ``trw_learn()``/``trw_recall()`` system. This section
    provides clear routing rules so the model uses each system for its
    strengths instead of defaulting to native features for everything.

    Uses table format for efficient side-by-side comparison, concrete routing
    examples for pattern-matching, and default-bias framing (trw_learn as
    the default action, native memory as the exception).

    Claude Code-specific \u2014 NOT included in AGENTS.md. Other platforms
    (opencode, Cursor, Aider) don't have native auto-memory to harmonize
    with, and their ``trw_learn()`` value proposition is already covered in
    ``render_agents_trw_section()``.

    Returns:
        Markdown string with memory routing guidance.
    """
    return (
        "### Memory Routing\n"
        "\n"
        "Default to `trw_learn()` for knowledge. "
        "Use native auto-memory only for personal preferences.\n"
        "\n"
        "| | `trw_learn()` | Native auto-memory |\n"
        "|---|---|---|\n"
        "| Search | `trw_recall(query)` \u2014 semantic + keyword | Filename scan only |\n"
        "| Visibility | All agents, subagents, teammates | Primary session only |\n"
        "| Lifecycle | Impact-scored, auto-promotes to CLAUDE.md | Static until manually edited |\n"
        "| Scale | Hundreds of entries, auto-pruned by staleness | 200-line index cap |\n"
        "\n"
        "Gotcha or error pattern \u2192 `trw_learn()`. "
        "User\u2019s preferred commit style \u2192 native memory. "
        "Build trick that saves time \u2192 `trw_learn()`. "
        "Communication preference \u2192 native memory.\n"
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
    return "### Session Boundaries\n\n" + _SESSION_BOUNDARY_TEXT + "\n"


def generate_behavioral_protocol_md() -> str:
    """Generate the full behavioral protocol as a static markdown file.

    PRD-CORE-093 FR03: This content is written to
    ``.trw/context/behavioral_protocol.md`` during ``update_project`` and
    read by the session-start hook once per session event. It replaces the
    verbose CLAUDE.md injection that previously loaded on every message.

    Returns:
        Complete markdown string for behavioral_protocol.md.
    """
    parts: list[str] = [
        "# TRW Behavioral Protocol\n",
        render_ceremony_quick_ref(),
        render_phase_descriptions(),
        render_ceremony_table(),
        render_ceremony_flows(),
        render_framework_reference(),
        render_closing_reminder(),
    ]
    return "\n".join(parts)


def render_minimal_protocol() -> str:
    """Render a shortened ceremony protocol for local model AGENTS.md.

    Must be under 200 tokens. Contains only:
    - Call trw_session_start() first
    - Call trw_deliver() when done
    - Run tests after each change
    """
    return (
        "TRW tools persist your work across sessions:\n"
        "- **Start**: call `trw_session_start()` to load prior learnings\n"
        "- **Finish**: call `trw_deliver()` to persist discoveries\n"
        "- **Verify**: Run tests after each change \u2014 fix failures before moving on.\n"
        "\n" + _SESSION_BOUNDARY_TEXT
    )


def render_agents_trw_section() -> str:
    """Render the complete TRW section for AGENTS.md — platform-generic.

    AGENTS.md is consumed by non-Claude Code platforms (opencode, local models,
    Cursor, Codex, Aider, etc.). Content must be:
    - Free of Claude Code-specific features (Agent Teams, subagents, slash commands)
    - Focused on MCP tools as the universal interface
    - Concise for smaller context windows (local models)
    - Self-contained (no references to Claude-specific FRAMEWORK.md)

    Returns:
        Complete markdown string for the TRW auto-generated section.
    """
    return (
        "TRW (The Real Work) is an engineering memory framework that persists "
        "patterns, gotchas, and project knowledge across sessions. It works "
        "with any AI coding assistant that supports MCP (Model Context Protocol).\n"
        "\n"
        "## TRW Tools\n"
        "\n"
        "These MCP tools are available when the TRW server is configured:\n"
        "\n"
        "- `trw_session_start()` \u2014 loads prior learnings and recovers any active run\n"
        "- `trw_checkpoint(message)` \u2014 saves progress so you can resume after interruptions\n"
        "- `trw_learn(summary, detail)` \u2014 records discoveries for all future sessions\n"
        "- `trw_deliver()` \u2014 persists everything when done "
        "(learnings, checkpoint, instruction sync)\n"
        "- `trw_recall(query)` \u2014 retrieves relevant learnings for a specific topic\n"
        "- `trw_build_check()` \u2014 runs lint, type-check, and tests to verify your work\n"
        "\n"
        "## Workflow\n"
        "\n"
        "1. **Start**: call `trw_session_start()` to load context from prior sessions\n"
        "2. **During**: call `trw_learn()` when you discover gotchas, patterns, or errors\n"
        "3. **During**: call `trw_checkpoint()` after milestones to save progress\n"
        "4. **Finish**: call `trw_deliver()` to persist your work for future sessions\n"
        "\n"
        "## Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT
    )


def render_codex_trw_section() -> str:
    """Render a Codex-specific TRW section for AGENTS.md."""
    return (
        "TRW (The Real Work) persists patterns, gotchas, and project knowledge across sessions via MCP.\n"
        "\n"
        "## Start Here\n"
        "\n"
        "- Call `trw_session_start()` first to load prior learnings and recover any active run\n"
        "- Read `.trw/frameworks/FRAMEWORK.md` early for the current project methodology\n"
        "- Use Codex subagents for bounded research, implementation, and review work\n"
        "\n"
        "## Core TRW Tools\n"
        "\n"
        "- `trw_session_start()` — load prior learnings and current run context\n"
        "- `trw_checkpoint(message)` — save milestone progress before context or direction shifts\n"
        "- `trw_learn(summary, detail)` — record durable discoveries for future sessions\n"
        "- `trw_recall(query)` — pull relevant project knowledge for the task at hand\n"
        "- `trw_build_check()` — run the project's build, lint, type-check, and test gates\n"
        "- `trw_deliver()` — persist work and sync instructions when the task is complete\n"
        "\n"
        "## Codex Workflow\n"
        "\n"
        "1. Start with `trw_session_start()`\n"
        "2. Delegate bounded work to Codex subagents when it improves focus\n"
        "3. Run tests and review the diff before completion\n"
        "4. Call `trw_learn()` for reusable gotchas or patterns\n"
        "5. Finish with `trw_deliver()` so future sessions inherit the result\n"
        "\n"
        "## OpenAI Docs\n"
        "\n"
        "If the task depends on current OpenAI or Codex behavior, use the OpenAI developer docs MCP server before relying on memory.\n"
        "\n"
        "## Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT
    )


def render_agent_teams_protocol() -> str:
    """Render Agent Teams protocol section for CLAUDE.md auto-generation.

    Provides teammates with dual-mode orchestration guidance, lifecycle
    expectations, and hook-based quality gates (PRD-INFRA-010).

    Returns:
        Markdown string with Agent Teams protocol, or empty string
        if the feature is not enabled.
    """
    config = get_config()

    if not config.agent_teams_enabled:
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


def render_codex_instructions() -> str:
    """Render instructions content for Codex .codex/INSTRUCTIONS.md.

    Returns:
        Markdown string for Codex-specific instructions.
    """
    return (
        "# Codex TRW Instructions\n"
        "\n"
        "## Framework Reference\n"
        "\n"
        "Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines the "
        "methodology your tools implement.\n"
        "\n"
        "## Codex Workflow\n"
        "\n"
        "1. **Start**: call `trw_session_start()` - loads all prior learnings\n"
        "2. **Delegate**: Use Codex subagents for bounded research, implementation, and review work\n"
        "3. **Verify**: Run tests after each change — fix failures before moving on\n"
        "4. **Learn**: Call `trw_learn()` for reusable gotchas or patterns\n"
        "5. **Finish**: call `trw_deliver()` - persists work for future sessions\n"
        "\n"
        "## Ceremony Protocol\n"
        "\n"
        "- `trw_checkpoint(message)` - saves progress so you can resume after context compaction\n"
        "- `trw_learn(summary, detail)` - records discoveries for all future sessions\n"
        "- `trw_deliver()` - persists everything in one call when done\n"
        "\n"
        "## Structured Output Conventions\n"
        "\n"
        "- Use structured output with typed Pydantic models when supported\n"
        "- Prefer typed tool arguments over freeform JSON\n"
        "- Validate outputs against schema before processing\n"
        "\n"
        "## Context Budget Guidance\n"
        "\n"
        "- Codex has 200K context budget\n"
        "- Use `trw_checkpoint()` to compact context before major changes\n"
        "- Leverage learnings store to reduce context for known patterns\n"
        "\n"
        "## Key Gotchas\n"
        "\n"
        "- **Context compaction**: Always checkpoint before context-heavy operations\n"
        "- **Test coverage**: Codex responds better to test-first instructions\n"
        "- **File navigation**: Be explicit about file paths\n"
        "- **Delegation**: Use subagents for better outcomes than direct implementation\n"
        "\n"
    )


def _load_prompting_guide(model_family: str) -> str:
    """Load bundled model-family prompting guide from package data.

    Args:
        model_family: One of 'qwen', 'gpt', 'claude', or 'generic'.

    Returns:
        Content of the prompting guide, or empty string on failure.
    """
    from importlib.resources import files as pkg_files

    filename = f"{model_family}.md"
    try:
        data_path = pkg_files("trw_mcp.data") / "prompting" / filename
        return data_path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError, TypeError):
        return ""


# Model-family display names and workflow headings.
_FAMILY_META: dict[str, tuple[str, str]] = {
    "qwen": ("Qwen-Coder-Next", "Qwen-Coder-Next Optimized Workflow"),
    "gpt": ("GPT-5.4", "GPT-5.4 Optimized Workflow"),
    "claude": ("Claude", "Claude Optimized Workflow"),
    "generic": ("Generic", "General Model Workflow"),
}

# Concise model-specific notes (non-generic families only).
_FAMILY_NOTES: dict[str, str] = {
    "qwen": (
        "### Qwen-Specific Notes\n"
        "\n"
        "- Qwen models work well with structured, explicit instructions\n"
        "- Use `/think` tags for complex reasoning when supported\n"
        "- Keep context concise — local models have smaller context windows\n"
    ),
    "gpt": (
        "### GPT-Specific Notes\n"
        "\n"
        "- GPT excels at multi-step reasoning and task decomposition\n"
        "- Leverage structured JSON output for typed results\n"
        "- Optimize for test coverage with test-first instruction patterns\n"
    ),
    "claude": (
        "### Claude-Specific Notes\n"
        "\n"
        "- Claude excels at file navigation and codebase understanding\n"
        "- Leverage Agent Teams for multi-file coordination\n"
        "- Use extended thinking for complex architectural decisions\n"
    ),
}


def render_opencode_instructions(model_family: str) -> str:
    """Render instructions content for OpenCode .opencode/INSTRUCTIONS.md.

    Model-family specific content to optimize instructions for Qwen, GPT, or Claude.

    Args:
        model_family: One of 'qwen', 'gpt', 'claude', or 'generic'.

    Returns:
        Markdown string for OpenCode-specific instructions.
    """
    family_name, workflow_title = _FAMILY_META.get(
        model_family, _FAMILY_META["generic"]
    )
    prompting_content = _load_prompting_guide(model_family)
    include_checkpoint = model_family != "generic"

    parts: list[str] = [
        f"# {family_name} TRW Instructions\n",
        "\n",
        "## Framework Reference\n",
        "\n",
        "Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines the "
        "methodology your tools implement.\n",
        "\n",
        f"## {workflow_title}\n",
        "\n",
        "1. **Start**: call `trw_session_start()` — loads all prior learnings\n",
        "2. **Delegate**: Use focused subagents for bounded tasks\n",
        "3. **Verify**: Run tests after each change — fix failures before moving on\n",
        "4. **Learn**: Call `trw_learn()` for reusable gotchas or patterns\n",
        "5. **Finish**: call `trw_deliver()` — persists work for future sessions\n",
        "\n",
    ]

    # Ceremony tools — checkpoint only for non-generic families.
    if include_checkpoint:
        parts.extend([
            "## Ceremony Protocol\n",
            "\n",
            "- `trw_checkpoint(message)` — saves progress so you can resume after context compaction\n",
            "- `trw_learn(summary, detail)` — records discoveries for all future sessions\n",
            "- `trw_deliver()` — persists everything in one call when done\n",
            "\n",
        ])

    parts.extend([
        "## Structured Output Conventions\n",
        "\n",
        "- Use structured output with typed Pydantic models\n",
        "- Prefer typed tool arguments over freeform JSON\n",
        "- Validate outputs against schema before processing\n",
        "\n",
        "## Context Budget Guidance\n",
        "\n",
        "- OpenCode has 200K context budget for cloud models, 128K for local models\n",
        "- Leverage learnings store to reduce context for known patterns\n",
        "\n",
    ])

    # Model-specific notes section (non-generic only).
    if model_family in _FAMILY_NOTES:
        parts.extend([_FAMILY_NOTES[model_family], "\n"])

    # Embedded prompting guide from bundled data.
    if prompting_content:
        parts.extend([
            "## Model-Specific Prompting Guide\n",
            "\n",
            prompting_content,
            "\n",
        ])

    return "".join(parts)
