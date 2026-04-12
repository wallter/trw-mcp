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
                # Use the example as the display call (keeps it concrete)
                lines.append(f"| `{ct.example}` | {ct.when} | {ct.what} |")
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
        lines.extend(
            f"- **{name}**: {purpose}" for name, purpose in PHASE_DESCRIPTIONS
        )
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
        lines.extend(
            f"| {ct.phase} | `{ct.tool}` | {ct.when} | {ct.what} | `{ct.example}` |"
            for ct in CEREMONY_TOOLS
        )
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

        PRD-CORE-131-FR02: Uses ``CEREMONY_TOOLS`` for the session protocol
        table rows instead of hardcoded strings.
        """
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

- Code patterns, gotchas, build tricks \u2192 `mcp_trw_trw_learn()`
- User preferences \u2192 Gemini's built-in `/memory add`

### Conventions

- Run tests after each change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_GEMINI_TRW_END_MARKER}
"""

    # ------------------------------------------------------------------
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
        """Render OpenCode instructions optimised for Qwen (local vLLM)."""
        return (
            "# Qwen-Coder-Next TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "Qwen models on vLLM have a **32K context window**. Keep instructions and "
            "prompts concise. Call `trw_checkpoint()` before context-heavy operations "
            "to compact the window. Do NOT paste large files inline \u2014 reference by path.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` \u2014 loads prior learnings\n"
            "2. **Think before acting**: for complex tasks, use `/think` tags to reason "
            "step-by-step before making tool calls\n"
            "3. **Keep tasks bounded**: 1-3 files per task is optimal for 32K context\n"
            "4. **Verify**: run tests after each change \u2014 fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` when you discover a durable technical pattern or gotcha (not routine status updates)\n"
            "6. **Finish**: call `trw_deliver()` \u2014 persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` \u2014 **first action** in every session\n"
            "- `trw_checkpoint(message)` \u2014 after each milestone; enables resume on crash\n"
            "- `trw_learn(summary, detail)` \u2014 for durable gotchas, patterns, error workarounds (no status reports)\n"
            "- `trw_deliver()` \u2014 **last action**; syncs instruction file and persists learnings\n"
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
            "debugging root-cause analysis. Skip for trivial edits (\u22643 lines).\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Issue direct tool calls \u2014 do not wrap in JSON envelopes\n"
            "- Use explicit file paths: never assume cwd\n"
            "- Prefer concise, targeted instructions over verbose context dumps\n"
            "- Validate tool arguments locally before issuing calls\n"
            "\n"
            "## Tool-Use Best Practices\n"
            "\n"
            "- Call one TRW tool at a time \u2014 vLLM streaming has a known parser bug that "
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
            "peer coordination \u2014 use focused sequential tasks instead\n"
            "- **Model tag required**: OpenCode requires exact model tag "
            "(e.g., `vllm/Qwen/Qwen3-Coder-Next-FP8`) \u2014 bare names silently fail\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines the "
            "methodology and quality gates your tools implement.\n"
        )

    def _render_opencode_gpt(self) -> str:
        """Render OpenCode instructions optimised for GPT models."""
        return (
            "# GPT TRW Instructions\n"
            "\n"
            "## Context Budget\n"
            "\n"
            "GPT-4o and GPT-5.x have a **128K+ context window**. Reasoning models "
            "(o3, o1) have extended token budgets for chain-of-thought. Use the full "
            "budget for complex multi-file tasks \u2014 no need to compress aggressively. "
            "Call `trw_checkpoint()` at phase boundaries.\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. **Start**: call `trw_session_start()` \u2014 loads prior learnings and "
            "active run state\n"
            "2. **Decompose**: use GPT's chain-of-thought to break tasks before acting\n"
            "3. **Delegate**: use focused subagents for bounded implementation tasks\n"
            "4. **Verify**: run tests after each change \u2014 fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)\n"
            "6. **Finish**: call `trw_deliver()` \u2014 persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` \u2014 **first action** in every session\n"
            "- `trw_checkpoint(message)` \u2014 after phase transitions; resume point if session ends\n"
            "- `trw_learn(summary, detail)` \u2014 record durable technical discoveries (not status)\n"
            "- `trw_build_check()` \u2014 at VALIDATE phase before marking work complete\n"
            "- `trw_deliver()` \u2014 **last action**; persists learnings and syncs instruction file\n"
            "\n"
            "## Reasoning Patterns\n"
            "\n"
            "GPT models respond well to explicit chain-of-thought framing:\n"
            "- State the problem and constraints before proposing a solution\n"
            "- For o3/o1 models, the model reasons internally \u2014 provide clear goals, "
            "not step-by-step instructions\n"
            "- For gpt-4o/5.x, explicit step-by-step instructions improve output quality\n"
            "- Use `trw_recall(query)` before designing solutions \u2014 prior agents may "
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
            "- For multi-PRD sprints, prefer `trw_init()` \u2192 run-scoped checkpointing\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **o3/o1 reasoning budget**: these models use internal reasoning tokens; "
            "verbose step-by-step prompts waste budget \u2014 provide goals, not procedures\n"
            "- **Tool call ordering**: GPT may try to do everything in one pass; "
            "prompt it to checkpoint at milestones explicitly\n"
            "- **Context window vs. quality**: larger context does not guarantee better "
            "output \u2014 targeted, scoped prompts outperform context-dump approaches\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines the "
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
            "1. **Start**: call `trw_session_start()` \u2014 loads prior learnings and "
            "any active run state\n"
            "2. **Assess**: determine task scope and decide whether to self-implement "
            "or delegate to subagents\n"
            "3. **Implement**: use focused subagents for tasks spanning 4+ files\n"
            "4. **Verify**: run tests after each change \u2014 fix failures before moving on\n"
            "5. **Learn**: call `trw_learn()` for technical discoveries (not routine status)\n"
            "6. **Finish**: call `trw_deliver()` \u2014 persists work for future sessions\n"
            "\n"
            "## Ceremony Protocol\n"
            "\n"
            "- `trw_session_start()` \u2014 **first action**; loads accumulated team knowledge\n"
            "- `trw_checkpoint(message)` \u2014 after milestones; resume point after compaction\n"
            "- `trw_learn(summary, detail)` \u2014 on discoveries; record durable technical insights (no status)\n"
            "- `trw_recall(query)` \u2014 before designing solutions; prior agents found gotchas\n"
            "- `trw_build_check()` \u2014 at VALIDATE and before DELIVER\n"
            "- `trw_deliver()` \u2014 **last action**; without this, session discoveries are lost\n"
            "\n"
            "## Reasoning Patterns\n"
            "\n"
            "Claude supports extended thinking for complex reasoning:\n"
            "- Enable extended thinking for: architectural decisions, root-cause analysis, "
            "multi-constraint optimisation\n"
            "- Use XML tags to structure complex prompts: "
            "`<task>`, `<context>`, `<constraints>`, `<output_format>`\n"
            "- Claude excels at reading and navigating codebases \u2014 provide file paths, "
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
            "- Parallel tool calls are safe with Claude \u2014 batch independent reads\n"
            "- `trw_review()` before delivery for sprint-scale work\n"
            "- Escalate to `trw_init()` for multi-PRD runs requiring phase tracking\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **Post-compaction ceremony skip**: after context compaction, sessions "
            "consistently skip ceremony \u2014 always call `trw_session_start()` explicitly "
            "at the start of every continuation\n"
            "- **Over-implementation**: Claude may implement directly rather than delegate \u2014 "
            "follow delegation guidance from FRAMEWORK.md for tasks spanning 4+ files\n"
            "- **structlog convention**: `event=` is a reserved kwarg; use alternative "
            "names in log calls\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines the "
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
            "1. **Start**: call `trw_session_start()` \u2014 loads prior learnings\n"
            "2. **Keep tasks bounded**: 1-3 files per task\n"
            "3. **Verify**: run tests after each change \u2014 fix failures before moving on\n"
            "4. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)\n"
            "5. **Finish**: call `trw_deliver()` \u2014 persists work for future sessions\n"
            "\n"
            "## TRW MCP Tools\n"
            "\n"
            "The following tools are available via MCP when the TRW server is configured:\n"
            "\n"
            "```\n"
            "trw_session_start()          \u2014 first action; loads prior learnings\n"
            "trw_checkpoint(message)      \u2014 save progress at milestones\n"
            "trw_learn(summary, detail)   \u2014 record technical discoveries (no status)\n"
            "trw_recall(query)            \u2014 retrieve relevant learnings\n"
            "trw_build_check()            \u2014 run lint, type-check, tests\n"
            "trw_deliver()                \u2014 last action; persists everything\n"
            "```\n"
            "\n"
            "## Structured Output Conventions\n"
            "\n"
            "- Issue direct tool calls with explicit, typed arguments\n"
            "- Use explicit file paths \u2014 never assume current working directory\n"
            "- Validate tool call results before proceeding to the next step\n"
            "\n"
            "## Tool-Use Best Practices\n"
            "\n"
            "- Call `trw_recall(query)` before re-implementing known patterns\n"
            "- Call `trw_build_check()` to verify your work before delivery\n"
            "- Issue tool calls one at a time if the model has parallel-call limitations\n"
            "\n"
            "## Known Limitations\n"
            "\n"
            "- **Unknown model capabilities**: test delegation patterns to understand "
            "model strengths before relying on them\n"
            "- **32K assumed budget**: if your model has a larger window, you can "
            "include more context per task\n"
            "- **Ceremony compliance**: models without explicit instruction-following "
            "training may skip ceremony; include explicit tool-call examples\n"
            "\n"
            "## Framework Reference\n"
            "\n"
            "Read `.trw/frameworks/FRAMEWORK.md` at session start \u2014 it defines the "
            "methodology your tools implement.\n"
        )
