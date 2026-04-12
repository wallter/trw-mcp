"""Unified renderer for TRW protocol instructions."""

from __future__ import annotations

from typing import Literal

from trw_mcp.state.claude_md._templates import CEREMONY_TOOLS


class ProtocolRenderer:
    """Centralizes protocol formatting for different AI coding assistants."""

    def __init__(
        self,
        platform: Literal["gemini", "claude", "opencode", "codex", "generic"] = "generic",
        model_family: Literal["qwen", "gpt", "claude", "generic"] = "generic",
        mode: Literal["full", "minimal", "compact"] = "full",
    ):
        self.platform = platform
        self.model_family = model_family
        self.mode = mode

    def render_table(self) -> str:
        """Render the Session Protocol table."""
        if self.mode == "minimal":
            return (
                "TRW tools persist your work across sessions:\n"
                "- **Start**: call `trw_session_start()` to load prior learnings\n"
                "- **Finish**: call `trw_deliver()` to persist discoveries (not status reports)\n"
                "- **Verify**: Run tests after each change \u2014 fix failures before moving on.\n"
            )

        if self.platform == "codex":
            # Codex prefers bullet points
            lines = [
                "- `trw_session_start()` \u2014 load prior learnings and current run context",
                "- `trw_checkpoint(message)` \u2014 save milestone progress before context or direction shifts",
                "- `trw_learn(summary, detail)` \u2014 record durable technical discoveries (no status reports)",
                "- `trw_recall(query)` \u2014 pull relevant project knowledge for the task at hand",
            ]
            return "\n".join(lines) + "\n"

        lines = [
            "| Tool | When | Why |",
            "|------|------|-----|",
        ]
        
        for t in CEREMONY_TOOLS:
            # Skip some tools for compact/generic to avoid clutter
            if self.mode == "compact" and t.tool not in ("trw_session_start", "trw_learn", "trw_checkpoint", "trw_deliver"):
                continue
                
            # Use specific instructions for trw_learn
            if t.tool == "trw_learn":
                what = "**CRITICAL: Only record actual insights, patterns, or gotchas.** NEVER record \"task completed\", \"PRD groomed\", or routine status updates. If you didn't learn a new technical pattern or find a non-obvious mistake to avoid, do NOT use this tool."
            else:
                what = t.what
                
            lines.append(f"| `{t.tool}` | {t.when} | {what} |")
            
        return "\n".join(lines) + "\n"

    def render_workflow(self) -> str:
        """Render the general workflow and reasoning patterns."""
        if self.platform == "opencode":
            learn_text = "5. **Learn**: call `trw_learn()` when you discover a durable technical pattern or gotcha (not routine status updates)\n"
            if self.model_family == "qwen":
                lines = [
                    "## Workflow\n",
                    "1. **Start**: call `trw_session_start()` — loads prior learnings and active run state",
                    "2. **Assess**: read PRDs, sprint docs, and `trw_status()` output",
                    "3. **Research**: use `codebase_investigator` for complex changes",
                    "4. **Implement**: make changes, run tests, and use `trw_checkpoint()` frequently",
                    learn_text,
                    "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n",
                    "## Ceremony Protocol\n",
                    "- `trw_session_start()` — **first action** in every session",
                    "- `trw_checkpoint(message)` — after each milestone; enables resume on crash",
                    "- `trw_learn(summary, detail)` — for durable gotchas, patterns, error workarounds (no status reports)",
                    "- `trw_deliver()` — **last action**; syncs instruction file and persists learnings\n",
                    "## Reasoning Patterns\n",
                    "Qwen3-Coder-Next supports `/think` ... `/think` blocks for extended reasoning:\n",
                    "- Use `/think` blocks to break down complex architectural problems before calling tools.",
                ]
            elif self.model_family == "gpt":
                lines = [
                    "## Workflow\n",
                    "1. **Start**: call `trw_session_start()` — loads prior learnings",
                    "2. **Assess**: read `trw_status()`",
                    "3. **Research**: use `codebase_investigator`",
                    "4. **Implement**: make changes, test, and `trw_checkpoint()`",
                    "5. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)",
                    "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n",
                    "## Ceremony Protocol\n",
                    "- `trw_session_start()` — **first action** in every session",
                    "- `trw_checkpoint(message)` — after phase transitions; resume point if session ends",
                    "- `trw_learn(summary, detail)` — record durable technical discoveries (not status)",
                    "- `trw_build_check()` — at VALIDATE phase before marking work complete",
                    "- `trw_deliver()` — **last action**; persists learnings and syncs instruction file\n",
                    "## Reasoning Patterns\n",
                    "GPT models respond well to explicit chain-of-thought framing:\n",
                    "- Step-by-step reasoning helps when resolving complex dependency graphs.",
                ]
            elif self.model_family == "claude":
                lines = [
                    "## Workflow\n",
                    "1. **Start**: call `trw_session_start()` — loads prior learnings",
                    "2. **Assess**: read `trw_status()`",
                    "3. **Research**: use `codebase_investigator`",
                    "4. **Implement**: make changes, test, and `trw_checkpoint()`",
                    "5. **Learn**: call `trw_learn()` for technical discoveries (not routine status)",
                    "6. **Finish**: call `trw_deliver()` — persists work for future sessions\n",
                    "## Ceremony Protocol\n",
                    "- `trw_session_start()` — **first action**; loads accumulated team knowledge",
                    "- `trw_checkpoint(message)` — after milestones; resume point after compaction",
                    "- `trw_learn(summary, detail)` — on discoveries; record durable technical insights (no status)",
                    "- `trw_recall(query)` — before designing solutions; prior agents found gotchas",
                    "- `trw_build_check()` — at VALIDATE and before DELIVER",
                    "- `trw_deliver()` — **last action**; without this, session discoveries are lost\n",
                    "## Reasoning Patterns\n",
                    "- Use `<thinking>` blocks to analyze code before tool usage.",
                ]
            else:
                lines = [
                    "## Workflow\n",
                    "1. **Start**: call `trw_session_start()`",
                    "2. **Research**: `trw_recall()` and `codebase_investigator`",
                    "3. **Implement**: write code and `trw_checkpoint()`",
                    "4. **Learn**: call `trw_learn()` for durable technical discoveries (no status reports)",
                    "5. **Finish**: call `trw_deliver()` — persists work for future sessions\n",
                    "## TRW MCP Tools\n",
                    "The following tools are available via MCP when the TRW server is configured:\n",
                    "```",
                    "trw_session_start()          — first action; loads prior learnings",
                    "trw_checkpoint(message)      — save progress at milestones",
                    "trw_learn(summary, detail)   — record technical discoveries (no status)",
                    "trw_recall(query)            — retrieve relevant learnings",
                    "trw_build_check()            — run lint, type-check, tests",
                    "trw_deliver()                — last action; persists everything",
                    "```\n",
                ]
            return "\n".join(lines) + "\n"

        return ""

