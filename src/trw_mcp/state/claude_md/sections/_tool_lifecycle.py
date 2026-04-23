"""Tool-lifecycle/instructions section renderers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
Houses: framework reference, closing reminder, Codex instructions,
OpenCode instructions, and the model-family prompting-guide loader.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: resolve ``get_config`` via the facade.
import trw_mcp.state.claude_md._static_sections as _facade
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import SESSION_BOUNDARY_TEXT as _SESSION_BOUNDARY_TEXT
from trw_mcp.state.claude_md._renderer import ProtocolRenderer


def render_framework_reference() -> str:
    """Render framework reference directive for CLAUDE.md."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_framework_reference()


def render_closing_reminder() -> str:
    """Render closing reminder with session boundaries and fallback guidance.

    PRD-FIX-073-FR03: Includes local CLI fallback troubleshooting.
    """
    return (
        "### Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT + "\n"
        "### Troubleshooting\n"
        "\n"
        "If MCP tools fail with 'fetch failed', use the local CLI fallback:\n"
        "- `trw-mcp local init --task NAME` to create a run directory\n"
        "- `trw-mcp local checkpoint --message MSG` to save progress\n"
        "\n"
    )


def render_codex_instructions() -> str:
    """Render instructions content for Codex .codex/INSTRUCTIONS.md."""
    return (
        "# Codex TRW Instructions\n"
        "\n"
        "## Instruction Sources\n"
        "\n"
        "- Codex reads `AGENTS.md` files before work, layering global and project guidance by directory precedence\n"
        "- TRW uses `.codex/INSTRUCTIONS.md` as the repo-local Codex instruction file\n"
        "- `.codex/agents/*.toml` custom agents are optional explicit helpers, not assumed background workers\n"
        "- Codex hooks are experimental and optional; core TRW correctness lives in the tools and middleware\n"
        "\n"
        "## Codex Workflow\n"
        "\n"
        "1. **Start**: call `trw_session_start()` — loads prior learnings and any active run\n"
        "2. **Delegate**: use custom agents or subagents only when you explicitly ask Codex to spawn them\n"
        "3. **Verify**: keep the working set small and run tests after each change before moving on\n"
        "4. **Learn**: Call `trw_learn()` for reusable gotchas or patterns\n"
        "5. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
        "\n"
        "## Ceremony Protocol\n"
        "\n"
        "- `trw_checkpoint(message)` — saves progress so you can resume after context compaction\n"
        "- `trw_learn(summary, detail)` — record durable technical discoveries (no status reports)\n"
        "- `trw_deliver()` — persists everything in one call when done\n"
        "\n"
        "## Runtime Guardrails\n"
        "\n"
        "- Prefer explicit file paths, concrete verification steps, and small diffs\n"
        "- Use custom agents or subagents only when you explicitly ask Codex to spawn them\n"
        "- Follow TRW tool and middleware guidance even when no hook fires\n"
        "- If current Codex behavior matters, check the OpenAI developer docs before assuming runtime details\n"
        "\n"
        "## Key Gotchas\n"
        "\n"
        "- **Context limits vary**: avoid hardcoding a fixed Codex context budget in plans or prompts\n"
        "- **Hooks are optional**: treat them as additive hints, not correctness gates\n"
        "- **Instruction discovery**: `AGENTS.md` layering and `.codex/INSTRUCTIONS.md` serve different roles\n"
        "- **File navigation**: be explicit about file paths and the repo root you are changing\n"
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
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family=model_family,
    )
    return renderer.render_opencode_instructions()
