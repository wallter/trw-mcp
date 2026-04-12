"""Unified ProtocolRenderer for generating instruction files."""

from __future__ import annotations

from typing import Literal

from trw_mcp.models.config import get_config
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._templates import CEREMONY_TOOLS, PHASE_DESCRIPTIONS

_SESSION_BOUNDARY_TEXT = (
    "Every session that loads learnings via `trw_session_start()` should persist "
    "them at session end \u2014 this is how your work compounds across sessions "
    "instead of being lost.\n"
)

_GEMINI_TRW_START_MARKER = "<!-- trw:gemini:start -->"
_GEMINI_TRW_END_MARKER = "<!-- trw:gemini:end -->"


class ProtocolRenderer:
    """Renders protocol instructions for different clients and models."""

    def __init__(
        self,
        client_profile: ClientProfile,
        model_family: str = "generic",
        ceremony_mode: Literal["FULL", "MINIMAL", "COMPACT"] = "FULL",
    ):
        self.client_profile = client_profile
        self.model_family = model_family
        self.ceremony_mode = ceremony_mode

    def render_ceremony_table(self) -> str:
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

    def render_ceremony_quick_ref(self) -> str:
        """Render compact ceremony quick-reference card for CLAUDE.md.

        Table format for scannability. Each tool gets when + what in one row.
        No redundancy with the imperative opener (which names tools briefly
        but doesn't explain them). Pointer to /trw-ceremony-guide for the
        full lifecycle reference.

        Returns:
            Markdown string with quick-reference table.
        """
        learn_why = "| `trw_learn(summary, detail)` | On discoveries | **CRITICAL: Only record actual technical insights.** NEVER record \"task completed\" or routine status updates |"
        if self.client_profile.client_id == "gemini":
            learn_why = "| `trw_learn(summary, detail)` | On discoveries | **CRITICAL: Only record actual insights, patterns, or gotchas.** NEVER record \"task completed\", \"PRD groomed\", or routine status updates. If you didn't learn a new technical pattern or find a non-obvious mistake to avoid, do NOT use this tool. |"

        return (
            "## TRW Behavioral Protocol (Auto-Generated)\n"
            "\n"
            "| Tool | When | Why |\n"
            "|------|------|-----|\n"
            "| `trw_session_start()` | First action | Loads prior learnings so you don't repeat solved problems or rediscover known gotchas |\n"
            f"{learn_why}\n"
            "| `trw_checkpoint(message)` | After milestones | If context compacts, you resume here instead of re-implementing from scratch |\n"
            "| `trw_deliver()` | Last action | Persists your session's discoveries for future agents — without it, your learnings die with your context window |\n"
            "\n"
            "Full tool lifecycle: `/trw-ceremony-guide`\n"
            "\n"
        )

    def render_opencode_instructions(self) -> str:
        """Render instructions content for OpenCode .opencode/INSTRUCTIONS.md.

        Produces materially different content for each model family so that
        context budget, reasoning syntax, tool-use patterns, and known
        limitations are appropriate for the detected model.

        Args:
            model_family: One of 'qwen', 'gpt', 'claude', or 'generic'.

        Returns:
            Markdown string for OpenCode-specific instructions.
        """
        if self.model_family == "qwen":
            return self._render_opencode_qwen()
        if self.model_family == "gpt":
            return self._render_opencode_gpt()
        if self.model_family == "claude":
            return self._render_opencode_claude()
        return self._render_opencode_generic()

    def _render_opencode_qwen(self) -> str:
        """Render OpenCode instructions optimised for Qwen (local vLLM)."""
        return (
            "# Qwen-Coder-Next TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "Qwen models on vLLM have a **32K context window**. Keep instructions and "
            "prompts concise. Call `trw_checkpoint()` before context-heavy operations "
            "to compact the window. Do NOT paste large files inline — reference by path.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` — loads prior learnings\n"
            "2. **Think before acting**: for complex tasks, use `/think` tags to reason "
            "step-by-step before making tool calls\n"
            "3. **Keep tasks bounded**: 1-3 files per task is optimal for 32K context\n"
            "4. **Verify**: run tests after each change — fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` when you discover a durable technical pattern or gotcha (not routine status updates)\n"
            "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` — **first action** in every session\n"
            "- `trw_checkpoint(message)` — after each milestone; enables resume on crash\n"
            "- `trw_learn(summary, detail)` — for durable gotchas, patterns, error workarounds (no status reports)\n"
            "- `trw_deliver()` — **last action**; syncs instruction file and persists learnings\n"
            "\n"
            "## Reasoning Patterns\n"
            "\n"
            "Qwen3-Coder-Next supports `/think` ... `/think` blocks for extended reasoning:\n"
            "```\n"
            "/think\n"
            "Break down what files need to change and why before making any edits.\n"
            "/think\n"
            "```\n"
            "Use `/think` blocks for: architectural decisions, multi-file refactors, "
            "debugging root-cause analysis. Skip for trivial edits (≤3 lines).\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Issue direct tool calls — do not wrap in JSON envelopes\n"
            "- Use explicit file paths: never assume cwd\n"
            "- Prefer concise, targeted instructions over verbose context dumps\n"
            "- Validate tool arguments locally before issuing calls\n"
            "\n"
            "## Tool-Use Best Practices\n"
            "\n"
            "- Call one TRW tool at a time — vLLM streaming has a known parser bug that "
            "can cause silent agent loop crashes on concurrent or malformed tool calls "
            "(anomalyco/opencode#16488)\n"
            "- If a tool call produces no output or the agent appears stuck, restart the "
            "session and call `trw_session_start()` to recover the last checkpoint\n"
            "- Prefer `trw_recall(query)` before re-implementing known patterns\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **32K context cap**: avoid loading large files; use targeted reads\n"
            "- **vLLM streaming bug**: tool call XML parsing can silently fail on "
            "concurrent calls; issue calls sequentially\n"
            "- **No Agent Teams**: Qwen via OpenCode does not support multi-agent "
            "peer coordination — use focused sequential tasks instead\n"
            "- **Model tag required**: OpenCode requires exact model tag "
            "(e.g., `vllm/Qwen/Qwen3-Coder-Next-FP8`) — bare names silently fail\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines the "
            "methodology and quality gates your tools implement.\n"
        )

    def _render_opencode_gpt(self) -> str:
        """Render OpenCode instructions optimised for GPT models (OpenAI API)."""
        return (
            "# GPT TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "GPT-4o and GPT-5.x have a **128K+ context window**. Reasoning models "
            "(o3, o1) have extended token budgets for chain-of-thought. Use the full "
            "budget for complex multi-file tasks — no need to compress aggressively. "
            "Call `trw_checkpoint()` at phase boundaries.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` — loads prior learnings and "
            "active run state\n"
            "2. **Decompose**: use GPT's chain-of-thought to break tasks before acting\n"
            "3. **Delegate**: use focused subagents for bounded implementation tasks\n"
            "4. **Verify**: run tests after each change — fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)\n"
            "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` — **first action** in every session\n"
            "- `trw_checkpoint(message)` — after phase transitions; resume point if session ends\n"
            "- `trw_learn(summary, detail)` — record durable technical discoveries (not status)\n"
            "- `trw_build_check()` — at VALIDATE phase before marking work complete\n"
            "- `trw_deliver()` — **last action**; persists learnings and syncs instruction file\n"
            "\n"
            "## Reasoning Patterns\n"
            "\n"
            "GPT models respond well to explicit chain-of-thought framing:\n"
            "- State the problem and constraints before proposing a solution\n"
            "- For o3/o1 models, the model reasons internally — provide clear goals, "
            "not step-by-step instructions\n"
            "- For gpt-4o/5.x, explicit step-by-step instructions improve output quality\n"
            "- Use `trw_recall(query)` before designing solutions — prior agents may "
            "have already solved the pattern\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Use structured JSON mode for typed outputs when available\n"
            "- Prefer typed Pydantic model arguments in tool calls\n"
            "- Validate outputs against schema before processing downstream\n"
            "- Use system message conventions: role-level context in system, "
            "task-specific context in user turn\n"
            "\n"
            "## Tool-Use Best Practices\n"
            "\n"
            "- Issue TRW tool calls in parallel where independent "
            "(e.g., `trw_recall` + file reads can run together)\n"
            "- `trw_prd_validate()` is required before marking PRD work as implemented\n"
            "- Use `trw_build_check(scope='full')` at VALIDATE, not just at delivery\n"
            "- For multi-PRD sprints, prefer `trw_init()` → run-scoped checkpointing\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **o3/o1 reasoning budget**: these models use internal reasoning tokens; "
            "verbose step-by-step prompts waste budget — provide goals, not procedures\n"
            "- **Tool call ordering**: GPT may try to do everything in one pass; "
            "prompt it to checkpoint at milestones explicitly\n"
            "- **Context window vs. quality**: larger context does not guarantee better "
            "output — targeted, scoped prompts outperform context-dump approaches\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines the "
            "6-phase execution model, exit criteria, and quality rubrics.\n"
        )

    def _render_opencode_claude(self) -> str:
        """Render OpenCode instructions optimised for Claude (Anthropic API)."""
        return (
            "# Claude TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "Claude models have a **200K context window**. Leverage the full budget "
            "for codebase understanding. Use extended thinking for complex architectural "
            "decisions. Call `trw_checkpoint()` at phase transitions so the session "
            "can be resumed after context compaction.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` — loads prior learnings and "
            "any active run state\n"
            "2. **Assess**: determine task scope and decide whether to self-implement "
            "or delegate to subagents\n"
            "3. **Implement**: use focused subagents for tasks spanning 4+ files\n"
            "4. **Verify**: run tests after each change — fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` for technical discoveries (not routine status)\n"
            "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` — **first action**; loads accumulated team knowledge\n"
            "- `trw_checkpoint(message)` — after milestones; resume point after compaction\n"
            "- `trw_learn(summary, detail)` — on discoveries; record durable technical insights (no status)\n"
            "- `trw_recall(query)` — before designing solutions; prior agents found gotchas\n"
            "- `trw_build_check()` — at VALIDATE and before DELIVER\n"
            "- `trw_deliver()` — **last action**; without this, session discoveries are lost\n"
            "\n"
            "## Reasoning Patterns\n"
            "\n"
            "Claude supports extended thinking for complex reasoning:\n"
            "- Enable extended thinking for: architectural decisions, root-cause analysis, "
            "multi-constraint optimisation\n"
            "- Use XML tags to structure complex prompts: "
            "`<task>`, `<context>`, `<constraints>`, `<output_format>`\n"
            "- Claude excels at reading and navigating codebases — provide file paths, "
            "not file contents, when possible\n"
            "- Leverage `trw_recall()` to load relevant learnings into context rather "
            "than re-reading source files for known patterns\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Use `tool_use` format for structured MCP tool invocations\n"
            "- XML tags improve instruction adherence: wrap constraints in `<constraints>`\n"
            "- Prefer Pydantic v2 model arguments over raw dicts in tool calls\n"
            "- `use_enum_values=True` required on models for YAML round-trip serialization\n"
            "\n"
            "## Tool-Use Best Practices\n"
            "\n"
            "- `trw_prd_validate()` is mandatory before marking PRD work implemented\n"
            "- Parallel tool calls are safe with Claude — batch independent reads\n"
            "- `trw_review()` before delivery for sprint-scale work\n"
            "- Escalate to `trw_init()` for multi-PRD runs requiring phase tracking\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **Post-compaction ceremony skip**: after context compaction, sessions "
            "consistently skip ceremony — always call `trw_session_start()` explicitly "
            "at the start of every continuation\n"
            "- **Over-implementation**: Claude may implement directly rather than delegate — "
            "follow delegation guidance from FRAMEWORK.md for tasks spanning 4+ files\n"
            "- **structlog convention**: `event=` is a reserved kwarg; use alternative "
            "names in log calls\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines the "
            "6-phase model, delegation decision tree, and quality rubrics. Re-read "
            "after context compaction.\n"
        )

    def _render_opencode_generic(self) -> str:
        """Render OpenCode instructions for unknown/generic models."""
        return (
            "# TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "Assuming a **32K context window** (unknown model). Keep instructions "
            "concise and tasks bounded (1-3 files per task). Reference files by path "
            "rather than pasting content inline.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` — loads prior learnings\n"
            "2. **Keep tasks bounded**: 1-3 files per task\n"
            "3. **Verify**: run tests after each change — fix failures before moving on\n"
            "4. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)\n"
            "5. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
            "\n"
            "## TRW MCP Tools\n"
            "\n"
            "The following tools are available via MCP when the TRW server is configured:\n"
            "\n"
            "```\n"
            "trw_session_start()          — first action; loads prior learnings\n"
            "trw_checkpoint(message)      — save progress at milestones\n"
            "trw_learn(summary, detail)   — record technical discoveries (no status)\n"
            "trw_recall(query)            — retrieve relevant learnings\n"
            "trw_build_check()            — run lint, type-check, tests\n"
            "trw_deliver()                — last action; persists everything\n"
            "```\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Issue direct tool calls with explicit, typed arguments\n"
            "- Use explicit file paths — never assume current working directory\n"
        )

    def render_behavioral_protocol(self) -> str:
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
            self.render_ceremony_quick_ref(),
            self.render_phase_descriptions(),
            self.render_ceremony_table(),
            self.render_ceremony_flows(),
            self.render_framework_reference(),
            self.render_closing_reminder(),
        ]
        return "\n".join(parts)

    def render_minimal_protocol(self) -> str:
        """Render a shortened ceremony protocol for local model AGENTS.md.

        Must be under 200 tokens. Contains only:
        - Call trw_session_start() first
        - Call trw_deliver() when done
        - Run tests after each change
        """
        return (
            "TRW tools persist your work across sessions:\n"
            "- **Start**: call `trw_session_start()` to load prior learnings\n"
            "- **Finish**: call `trw_deliver()` to persist discoveries (not status reports)\n"
            "- **Verify**: Run tests after each change \u2014 fix failures before moving on.\n"
            "\n" + _SESSION_BOUNDARY_TEXT
        )

    def render_gemini_instructions(self) -> str:
        """Generate GEMINI.md TRW ceremony section."""
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

- Run tests after each change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_GEMINI_TRW_END_MARKER}
"""

    def render_phase_descriptions(self) -> str:
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

    def render_ceremony_flows(self) -> str:
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

    def render_framework_reference(self) -> str:
        """Render framework reference directive for CLAUDE.md.

        PRD-CORE-125-FR10: Gated by ``include_framework_ref`` on client profile.

        Points agents to the methodology document. Compact per PRD-CORE-061
        progressive disclosure \u2014 the framework itself explains why, this
        section just says where and when to read it.

        Returns:
            Markdown string with framework pointer and reading schedule,
            or empty string if disabled.
        """
        config = get_config()
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

    def render_closing_reminder(self) -> str:
        """Render closing reminder that bookends the auto-generated section.

        Position bias research (Liu et al. 2024) shows the end of a prompt
        gets elevated attention weight. This repeats the two most-skipped
        ceremony tools in a different semantic frame from the imperative opener.

        Returns:
            Markdown string with closing reminder.
        """
        return "### Session Boundaries\n\n" + _SESSION_BOUNDARY_TEXT + "\n"
