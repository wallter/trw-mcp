"""Static CLAUDE.md section renderers — protocol, ceremony, delegation, watchlist."""

from __future__ import annotations

import contextvars
import time
from typing import NamedTuple

import structlog
import yaml as _yaml
from trw_memory.graph import list_org_shared_entries
from trw_memory.models.config import MemoryConfig

from trw_mcp.models.config import get_config
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.claude_md._renderer import SESSION_BOUNDARY_TEXT as _SESSION_BOUNDARY_TEXT
from trw_mcp.state.claude_md._renderer import ProtocolRenderer
from trw_mcp.state.persistence import FileStateReader

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FR01: Turn-scoped analytics cache (PRD-FIX-072)
# ---------------------------------------------------------------------------

_ANALYTICS_TTL_SECONDS = 5.0


class _AnalyticsCacheEntry(NamedTuple):
    path: str
    sessions: int
    learnings: int
    ts: float


_analytics_cache: contextvars.ContextVar[_AnalyticsCacheEntry | None] = contextvars.ContextVar(
    "_analytics_cache",
    default=None,
)


def _safe_int(value: object) -> int:
    """Coerce an analytics field value to int, returning 0 on failure."""
    try:
        return int(str(value or 0))
    except (ValueError, TypeError):
        return 0


def _load_analytics_counts() -> tuple[int, int]:
    """Return tracked session and learning counts from analytics.yaml.

    Uses a ContextVar-backed cache with a short TTL to avoid re-parsing
    the YAML file on every instruction render within a single tool turn.
    """
    logger = structlog.get_logger(__name__)
    config = get_config()
    analytics_path = resolve_project_root() / config.trw_dir / config.context_dir / "analytics.yaml"
    analytics_key = str(analytics_path)
    cached = _analytics_cache.get()
    if (
        cached is not None
        and cached.path == analytics_key
        and (time.monotonic() - cached.ts) < _ANALYTICS_TTL_SECONDS
    ):
        return cached.sessions, cached.learnings

    if not analytics_path.exists():
        entry = _AnalyticsCacheEntry(
            path=analytics_key,
            sessions=0,
            learnings=0,
            ts=time.monotonic(),
        )
        _analytics_cache.set(entry)
        return 0, 0

    # FR03: Specific exception handling (PRD-FIX-072)
    try:
        data = FileStateReader().read_yaml(analytics_path)
        sessions = _safe_int(data.get("sessions_tracked", 0))
        learnings = _safe_int(data.get("total_learnings", 0))
        entry = _AnalyticsCacheEntry(
            path=analytics_key,
            sessions=sessions,
            learnings=learnings,
            ts=time.monotonic(),
        )
        _analytics_cache.set(entry)
        return sessions, learnings
    except FileNotFoundError:
        logger.debug("analytics_file_not_found", path=str(analytics_path))
    except _yaml.YAMLError:
        logger.warning("analytics_parse_error", path=str(analytics_path), exc_info=True)
    except OSError:
        logger.warning("analytics_read_error", path=str(analytics_path), exc_info=True)

    entry = _AnalyticsCacheEntry(
        path=analytics_key,
        sessions=0,
        learnings=0,
        ts=time.monotonic(),
    )
    _analytics_cache.set(entry)
    return 0, 0


def _format_learning_session_claim() -> str:
    """Render a truthful analytics-backed learning/session claim."""
    sessions_tracked, total_learnings = _load_analytics_counts()
    session_label = "session" if sessions_tracked == 1 else "sessions"
    learning_label = "learning" if total_learnings == 1 else "learnings"
    return f"{total_learnings} {learning_label} from {sessions_tracked} prior {session_label}"


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
    analytics_claim = _format_learning_session_claim()
    return (
        "Your primary role is **orchestration** \u2014 delegate to focused agents "
        "for better outcomes than direct implementation. Focused subagents produce "
        "fewer defects because they get deeper context per task. Reserve "
        "self-implementation for trivial edits (\u22643 lines, 1 file).\n"
        "\n"
        "**Your first action in every session must be `trw_session_start()`.**\n"
        "\n"
        f"This single call loads everything you need: {analytics_claim}, "
        "any active run state you can resume, "
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
    """Render compact ceremony quick-reference card for CLAUDE.md."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile)
    return renderer.render_ceremony_quick_ref()


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml."""
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
    lines = [f"- {d}" for d in directives[:12]]
    lines.append("")
    return "\n".join(lines) + "\n"


def render_phase_descriptions() -> str:
    """Render phase arrow diagram and description list."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile)
    return renderer.render_phase_descriptions()


def render_ceremony_table() -> str:
    """Render ceremony tools as a markdown table."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile)
    return renderer.render_ceremony_table()


def render_ceremony_flows() -> str:
    """Render quick task and full run example flows."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile)
    return renderer.render_ceremony_flows()


def render_delegation_protocol() -> str:
    """Render delegation discipline section for CLAUDE.md auto-generation.

    PRD-CORE-125-FR10: Gated by ``include_delegation`` on client profile.

    Provides a compact delegation decision tree and mode comparison so
    agents default to delegation for non-trivial tasks. Uses value-oriented
    framing (why delegation produces better results) rather than prescriptive
    mandates (MUST/NEVER).

    Returns:
        Markdown string with delegation guidance, or empty string if disabled.
    """
    config = get_config()
    if not config.client_profile.include_delegation:
        return ""

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
        "| Agents who skip recall consistently re-discover known gotchas, spending 2-3x the time |\n"
        '| "I can implement directly, delegation is overhead" '
        "| Focused subagents produce fewer defects "
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
    """Render framework reference directive for CLAUDE.md."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile)
    return renderer.render_framework_reference()


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
    sessions_tracked, total_learnings = _load_analytics_counts()
    scale_claim = f"{total_learnings} learnings across {sessions_tracked} sessions"
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
        "| Lifecycle | Impact-scored, recalled at session start | Static until manually edited |\n"
        f"| Scale | {scale_claim}, auto-pruned by staleness | 200-line index cap |\n"
        "\n"
        "Gotcha or error pattern \u2192 `trw_learn()`. "
        "User\u2019s preferred commit style \u2192 native memory. "
        "Build trick that saves time \u2192 `trw_learn()`. "
        "Communication preference \u2192 native memory.\n"
        "\n"
    )


def render_shared_learnings() -> str:
    """Render top cross-validated org learnings when sibling projects exist."""
    try:
        entries = list_org_shared_entries(
            MemoryConfig(),
            "project:default",
            min_importance=0.7,
            limit=5,
        )
    except Exception:  # justified: fail-open — graph backend may not be available
        _logger.debug("shared_learnings_unavailable", exc_info=True)
        return ""

    if not entries:
        return ""

    lines = [
        "## Shared Learnings",
        "",
    ]
    for entry in entries:
        summary = entry.detail.splitlines()[0].strip() if entry.detail.strip() else entry.content
        lines.append(f"- **{entry.content}** — {summary}")
    lines.append("")
    return "\n".join(lines)


def render_closing_reminder() -> str:
    """Render closing reminder with session boundaries and fallback guidance.

    PRD-FIX-073-FR03: Includes local CLI fallback troubleshooting.
    """
    return (
        "### Session Boundaries\n"
        "\n"
        + _SESSION_BOUNDARY_TEXT
        + "\n"
        "### Troubleshooting\n"
        "\n"
        "If MCP tools fail with 'fetch failed', use the local CLI fallback:\n"
        "- `trw-mcp local init --task NAME` to create a run directory\n"
        "- `trw-mcp local checkpoint --message MSG` to save progress\n"
        "\n"
    )


def generate_behavioral_protocol_md() -> str:
    """Generate the full behavioral protocol as a static markdown file."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile, ceremony_mode="FULL")
    return renderer.render_behavioral_protocol()


def render_minimal_protocol() -> str:
    """Render a shortened ceremony protocol for local model AGENTS.md."""
    renderer = ProtocolRenderer(client_profile=get_config().client_profile, ceremony_mode="MINIMAL")
    return renderer.render_minimal_protocol()


def render_agents_trw_section(
    exposed_tools: frozenset[str] | set[str] | None = None,
) -> str:
    """Render the complete TRW section for AGENTS.md — platform-generic.

    AGENTS.md is consumed by non-Claude Code platforms (opencode, local models,
    Cursor, Codex, Aider, etc.). Content must be:
    - Free of Claude Code-specific features (Agent Teams, subagents, slash commands)
    - Focused on MCP tools as the universal interface
    - Concise for smaller context windows (local models)
    - Self-contained (no references to Claude-specific FRAMEWORK.md)

    Args:
        exposed_tools: When provided, only include descriptions for tools in
            this set. None renders all tools (backward compatible).

    Returns:
        Complete markdown string for the TRW auto-generated section.
    """
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
        "\n"
        + tool_list
        + "\n"
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
    """Render a Codex-specific TRW section for AGENTS.md.

    Args:
        exposed_tools: When provided, only include descriptions for tools in
            this set. None renders all tools (backward compatible).
    """
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
        "\n"
        + tool_list
        + "\n"
        "## Codex Workflow\n"
        "\n"
        "1. Start with `trw_session_start()`\n"
        "2. Keep the working set small and call `trw_checkpoint()` before context-heavy turns or major pivots\n"
        "3. Run tests and review the diff before completion\n"
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


def render_agent_teams_protocol() -> str:
    """Render Agent Teams protocol section for CLAUDE.md auto-generation.

    PRD-CORE-125-FR10: Also gated by ``include_agent_teams`` on client profile.

    Provides teammates with dual-mode orchestration guidance, lifecycle
    expectations, and hook-based quality gates (PRD-INFRA-010).

    Returns:
        Markdown string with Agent Teams protocol, or empty string
        if the feature is not enabled.
    """
    config = get_config()

    if not config.agent_teams_enabled:
        return ""

    if not config.client_profile.include_agent_teams:
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
