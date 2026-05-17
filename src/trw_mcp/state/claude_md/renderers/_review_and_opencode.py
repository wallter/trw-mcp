"""OpenCode instruction renderers.

The public wrapper names are kept for compatibility, but v25 emits a
portable instruction body instead of model-family-specific prompt text.
"""

from __future__ import annotations

# Shared Gemini marker constants (mirror ``_renderer.py``).
_GEMINI_TRW_START_MARKER = "<!-- trw:gemini:start -->"
_GEMINI_TRW_END_MARKER = "<!-- trw:gemini:end -->"


def render_gemini_instructions() -> str:
    """Render GEMINI.md TRW ceremony section."""
    return f"""{_GEMINI_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

## TRW Framework Integration

This project uses the [TRW Framework](https://trwframework.com) for structured
AI-assisted development. TRW gives your Gemini CLI sessions persistent engineering
memory — patterns, gotchas, and project knowledge accumulate across sessions.

### Session Protocol

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings |
| `trw_learn(summary, detail)` | On discoveries | **CRITICAL: Only record actual insights, patterns, or gotchas.** NEVER record "task completed", "PRD groomed", or routine status updates. If you didn't learn a new technical pattern or find a non-obvious mistake to avoid, do NOT use this tool. |
| `trw_checkpoint(message)` | After milestones | Resume point if context compacts |
| `trw_deliver()` | Last action | Persists session work |

### MCP Tools

All TRW tools are available via MCP as `mcp_trw_<tool_name>`.
Call `mcp_trw_trw_session_start` first in every session.

Key tools: `trw_session_start`, `trw_learn`, `trw_checkpoint`, `trw_deliver`,
`trw_init`, `trw_status`, `trw_recall`, `trw_build_check`, `trw_review`,
`trw_prd_create`, `trw_prd_validate`.

### Subagents

TRW provides specialized agents in `.gemini/agents/`:
- `@trw-explorer` — Fast codebase search and analysis (read-only)
- `@trw-implementer` — TDD implementation with full tool access
- `@trw-reviewer` — Code review specialist (read-only)
- `@trw-lead` — Orchestration and delegation

### Memory Routing

- Code patterns, gotchas, build tricks → `mcp_trw_trw_learn()`
- User preferences → Gemini's built-in `/memory add`

### Conventions

- Run the project-native validation command after each meaningful change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_GEMINI_TRW_END_MARKER}
"""


def _render_opencode_portable() -> str:
    """Render OpenCode instructions without model/provider assumptions."""
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
        "6. **Finish**: call `trw_deliver()` only after `trw_build_check()` records validation or you explicitly label an acceptable failure\n"
        "\n"
        "## Ceremony Protocol\n"
        "\n"
        "- `trw_session_start()` — **first action** in every session\n"
        "- `trw_checkpoint(message)` — save progress at meaningful milestones\n"
        "- `trw_recall(query)` — retrieve relevant prior learnings before re-solving\n"
        "- `trw_learn(summary, detail)` — record durable gotchas/patterns, not routine status\n"
        "- `trw_build_check()` — record project-native validation before delivery after code changes\n"
        "- `trw_deliver()` — **last action after validation**; persists learnings and syncs instruction state\n"
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
