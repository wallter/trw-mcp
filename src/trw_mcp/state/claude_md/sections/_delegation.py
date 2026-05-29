"""Delegation, watchlist, coordination, and AGENTS.md section renderers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: resolve ``get_config`` via the facade.
import trw_mcp.state.claude_md._static_sections as _facade
from trw_mcp.state.claude_md._renderer import SESSION_BOUNDARY_TEXT as _SESSION_BOUNDARY_TEXT
from trw_mcp.state.claude_md.sections._memory_routing import _load_analytics_counts


def render_delegation_protocol() -> str:
    """Render model- and harness-neutral delegation discipline guidance."""
    config = _facade.get_config()
    if not config.client_profile.include_delegation:
        return ""

    return (
        "## TRW Delegation & Orchestration (Auto-Generated)\n"
        "\n"
        "As orchestrator, your responsibilities are: (1) assess and decompose tasks, "
        "(2) use focused helpers only when the active harness supports them, "
        "(3) verify integration and quality, (4) maintain strategic oversight, "
        "and (5) preserve knowledge via TRW tools. Direct implementation is reserved "
        "for small or tightly coupled edits.\n"
        "\n"
        "### When to Delegate\n"
        "\n"
        "```\n"
        "Task arrives → Assess scope and harness\n"
        "├── Trivial or tightly coupled?   → Self-implement with checkpoint\n"
        "├── Research/read-only?           → Focused helper if available; otherwise sequential shard\n"
        "├── Single-scope? (≤3 files)      → One helper or one local pass\n"
        "├── Multi-scope? (4+ files)       → Split by explicit file ownership\n"
        "└── Interdependent/high-risk?     → Plan contracts first, then implement/review in stages\n"
        "```\n"
        "\n"
        "Delegation is optional. The invariant is focused context, explicit file "
        "ownership, persisted findings, and final integration by the orchestrator. "
        "If the client has no safe delegation surface, execute the same shards "
        "sequentially in the current session.\n"
        "\n"
    )


def render_rationalization_watchlist() -> str:
    """Render anti-rationalization watchlist and rigid/flexible classification."""
    return (
        "## Rationalization Watchlist (Auto-Generated)\n"
        "\n"
        "If you catch yourself thinking any of these, stop and follow the process:\n"
        "\n"
        "| Thought | Why it's wrong | Consequence |\n"
        "|---------|---------------|-------------|\n"
        '| "This is too simple for ceremony" '
        "| Simple tasks still lose state when sessions skip persistence "
        "| You skip checkpoint → context compacts → you re-implement from scratch |\n"
        '| "I\'ll checkpoint/deliver after I finish this part" '
        "| Context compaction erases uncheckpointed work permanently "
        "| Prior sessions that skipped trw_deliver lost reusable learnings |\n"
        '| "I already know the codebase" '
        "| Prior learnings contain gotchas for exactly this area "
        "| Skipping recall causes known gotchas to be rediscovered instead of reused |\n"
        '| "I can implement directly, delegation is overhead" '
        "| Focused helper shards can preserve context when the harness supports them "
        "| Your focused context is valuable — split only when ownership is clear |\n"
        '| "The build check can wait until the end" '
        "| Late build failures cascade into multi-file rework "
        "| 2x rework when caught at DELIVER vs catching at VALIDATE |\n"
        "\n"
        "### Rigid Tools (unconditional — the cost of skipping exceeds the cost of running)\n"
        "\n"
        "- `trw_session_start()` — first action; loads accumulated knowledge so you start from the project's accumulated experience, not zero\n"
        "- `trw_deliver()` — last action; without this, your session's discoveries are invisible to every future session\n"
        "- `trw_build_check()` — at VALIDATE and before DELIVER; late-caught bugs cascade into 2x rework\n"
        "- Completion artifacts — before marking complete; false completion reports cause downstream work to build on a foundation that doesn't exist\n"
        "\n"
        "### Flexible Tools (must happen, you choose the moment)\n"
        "\n"
        "- `trw_checkpoint()` — at milestones; your last checkpoint is your resume point after context compaction\n"
        "- `trw_learn()` — on discoveries; every learning you skip forces a future session to rediscover it\n"
        "- `trw_recall()` — at start; prior agents already found the gotchas for your current task\n"
        "\n"
    )


def render_agents_trw_section(
    exposed_tools: frozenset[str] | set[str] | None = None,
) -> str:
    """Render the complete TRW section for AGENTS.md — platform-generic."""
    from trw_mcp.state.claude_md._tool_manifest import render_tool_list

    sessions_tracked, total_learnings = _load_analytics_counts()
    session_label = "session" if sessions_tracked == 1 else "sessions"

    tool_list = render_tool_list(exposed_tools)

    return (
        "TRW (The Real Work) is an engineering memory framework that persists "
        "patterns, gotchas, and project knowledge across sessions. It works "
        "with any AI coding assistant that supports MCP (Model Context Protocol).\n"
        "\n"
        "## TRW Tools\n"
        "\n"
        "These MCP tools are available when the TRW server is configured:\n"
        "\n" + tool_list + "\n"
        "## Workflow\n"
        "\n"
        f"1. **Start**: call `trw_session_start()` — it loads {total_learnings} learnings from {sessions_tracked} prior {session_label} and recovers any active run; use it to load context from {sessions_tracked} prior {session_label}\n"
        "2. **During**: call `trw_learn()` when you discover gotchas, patterns, or errors\n"
        "3. **During**: call `trw_checkpoint()` after milestones to save progress\n"
        "4. **Finish**: call `trw_deliver()` to persist your work for future sessions\n"
        "\n"
        "## Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT
    )


def render_codex_trw_section(
    exposed_tools: frozenset[str] | set[str] | None = None,
) -> str:
    """Render a Codex-specific TRW section for AGENTS.md."""
    from trw_mcp.state.claude_md._tool_manifest import render_tool_list

    tool_list = render_tool_list(exposed_tools)

    return (
        "TRW (The Real Work) persists patterns, gotchas, and project knowledge across sessions via MCP.\n"
        "\n"
        "## Start Here\n"
        "\n"
        "- Call `trw_session_start()` first to load prior learnings and recover any active run\n"
        "- Treat `AGENTS.md` and `.codex/INSTRUCTIONS.md` as the main Codex instruction surfaces for this repo\n"
        "- If the task depends on current Codex behavior, check the OpenAI developer docs MCP server before relying on memory\n"
        "\n"
        "## Core TRW Tools\n"
        "\n" + tool_list + "\n"
        "## Codex Workflow\n"
        "\n"
        "1. Start with `trw_session_start()`\n"
        "2. Keep the working set small and call `trw_checkpoint()` before context-heavy turns or major pivots\n"
        "3. Run project-native validation and review the diff before completion\n"
        "4. Use custom agents or subagents only when you explicitly ask Codex to spawn them\n"
        "5. Finish with `trw_deliver()` so future sessions inherit the result\n"
        "\n"
        "## Runtime Notes\n"
        "\n"
        "- Codex reads `AGENTS.md` files from global/project/current-directory scope in precedence order, subject to runtime size limits\n"
        "- `.codex/agents/*.toml` custom agents are explicit helpers; do not assume hidden background delegation\n"
        "- Hooks are experimental and optional; core ceremony guarantees come from TRW tools and middleware rather than hook interception\n"
        "\n"
        "## OpenAI Docs\n"
        "\n"
        "If the task depends on current OpenAI or Codex behavior, use the OpenAI developer docs MCP server before relying on memory.\n"
        "\n"
        "## Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT
    )
