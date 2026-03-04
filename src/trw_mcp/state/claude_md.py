"""CLAUDE.md rendering and sync — template loading, section generation, marker-based merge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()

# Named caps for list truncation (not user-tunable)
CLAUDEMD_LEARNING_CAP = 10
CLAUDEMD_PATTERN_CAP = 5
BEHAVIORAL_PROTOCOL_CAP = 12


class CeremonyTool(NamedTuple):
    """A lifecycle-critical MCP tool with usage guidance."""

    phase: str
    tool: str
    when: str
    what: str
    example: str


# Phase descriptions for the 6-phase execution model
PHASE_DESCRIPTIONS: list[tuple[str, str]] = [
    ("RESEARCH", "Discover context, audit codebase, register findings"),
    ("PLAN", "Design implementation approach, identify dependencies"),
    ("IMPLEMENT", "Execute work with periodic checkpoints, shard self-review before completing"),
    ("VALIDATE", "Run trw_build_check, verify coverage, lead checks shard integration"),
    ("REVIEW", "Review diff for quality (DRY/KISS/SOLID), fix gaps, record learnings"),
    ("DELIVER", "Sync artifacts, checkpoint, close run"),
]

# 11 lifecycle-critical tools in execution order
CEREMONY_TOOLS: list[CeremonyTool] = [
    CeremonyTool("Start", "trw_session_start", "At session start — loads learnings + run state (pass query for focused recall)", "Recall learnings + check run status", "trw_session_start(query='task domain')"),
    CeremonyTool("Start", "trw_recall", "Quick tasks — retrieves relevant prior learnings", "Search learnings by query", "trw_recall('*', min_impact=0.7)"),
    CeremonyTool("Start", "trw_status", "When resuming — shows phase, progress, next steps", "Show run state and phase", "trw_status()"),
    CeremonyTool("RESEARCH", "trw_init", "New tasks — creates run directory for tracking", "Bootstrap run directory + events", "trw_init(task_name='...')"),
    CeremonyTool("Any", "trw_learn", "On errors/discoveries — saves for future sessions", "Record learning entry", "trw_learn(summary='...', impact=0.8)"),
    CeremonyTool("Any", "trw_checkpoint", "After milestones — preserves progress across compactions", "Atomic state snapshot", "trw_checkpoint(message='...')"),
    CeremonyTool("PLAN", "trw_prd_create", "When defining requirements", "Generate AARE-F PRD", "trw_prd_create(input_text='...')"),
    CeremonyTool("PLAN", "trw_prd_validate", "Before implementation", "PRD quality gate", "trw_prd_validate(prd_path='...')"),
    CeremonyTool("VALIDATE", "trw_build_check", "After implementation — runs pytest + mypy, verifies integration", "Run pytest + mypy", "trw_build_check(scope='full')"),
    CeremonyTool("REVIEW", "review diff", "After VALIDATE — check quality (DRY/KISS/SOLID), fix gaps, record learnings", "Review diff, fix incomplete integrations", "Read diff, fix gaps, trw_learn(summary='...')"),
    CeremonyTool("DELIVER", "trw_claude_md_sync", "At delivery — promotes learnings to CLAUDE.md", "Promote learnings to CLAUDE.md", "trw_claude_md_sync()"),
    CeremonyTool("DELIVER", "trw_deliver", "At task completion — persists everything in one call", "reflect+sync+checkpoint+index", "trw_deliver()"),
]


# CLAUDE.md TRW section markers (must stay consistent — parsing depends on these)
TRW_AUTO_COMMENT = "<!-- TRW AUTO-GENERATED \u2014 do not edit between markers -->"
TRW_MARKER_START = "<!-- trw:start -->"
TRW_MARKER_END = "<!-- trw:end -->"


def load_claude_md_template(trw_dir: Path) -> str:
    """Load CLAUDE.md template: .trw/templates/ > bundled > inline fallback.

    Resolution order:
    1. Project-local: ``trw_dir / templates_dir / "claude_md.md"``
    2. Bundled: ``data/templates/claude_md.md`` in package
    3. Inline fallback (minimal markers only)

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Template string with ``{{placeholder}}`` tokens.
    """
    # 1. Project-local override
    project_template = trw_dir / _config.templates_dir / "claude_md.md"
    if project_template.exists():
        return project_template.read_text(encoding="utf-8")

    # 2. Bundled template
    data_dir = Path(__file__).parent.parent / "data" / "templates"
    bundled = data_dir / "claude_md.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")

    # 3. Inline fallback
    return (
        "\n"
        f"{TRW_AUTO_COMMENT}\n"
        f"{TRW_MARKER_START}\n"
        "\n"
        "{{imperative_opener}}"
        "{{ceremony_quick_ref}}"
        "{{delegation_section}}"
        "{{agent_teams_section}}"
        "{{behavioral_protocol}}"
        "{{rationalization_watchlist}}"
        "{{ceremony_phases}}"
        "{{ceremony_table}}"
        "{{ceremony_flows}}"
        "{{architecture_section}}"
        "{{conventions_section}}"
        "{{categorized_learnings}}"
        "{{patterns_section}}"
        "{{adherence_section}}"
        "{{closing_reminder}}"
        f"{TRW_MARKER_END}\n"
    )


def render_template(template: str, context: dict[str, str]) -> str:
    """Replace ``{{placeholder}}`` tokens and collapse empty sections.

    Args:
        template: Template string with ``{{key}}`` placeholders.
        context: Mapping of placeholder names to rendered content.

    Returns:
        Rendered markdown string with empty sections collapsed.
    """
    result = template
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", value)
    # Collapse runs of 3+ consecutive blank lines to 2
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


def _render_context_section(
    heading: str,
    data: dict[str, object],
    skip_keys: frozenset[str],
) -> str:
    """Render a context data dict as a markdown section with bullet items.

    Args:
        heading: Section heading (e.g. "Architecture", "Conventions").
        data: Key-value data from a context YAML file.
        skip_keys: Keys to exclude from the output.

    Returns:
        Markdown string or empty string if no data.
    """
    if not data:
        return ""
    lines: list[str] = [f"### {heading}"]
    for key, val in data.items():
        if val and key not in skip_keys:
            lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines) + "\n"


_ARCH_SKIP_KEYS = frozenset({"notes"})
_CONV_SKIP_KEYS = frozenset({"notes", "test_patterns"})


def render_architecture(arch_data: dict[str, object]) -> str:
    """Render architecture context to markdown.

    Args:
        arch_data: Architecture data from context/architecture.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    return _render_context_section("Architecture", arch_data, _ARCH_SKIP_KEYS)


def render_conventions(conv_data: dict[str, object]) -> str:
    """Render conventions context to markdown.

    Args:
        conv_data: Conventions data from context/conventions.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    return _render_context_section("Conventions", conv_data, _CONV_SKIP_KEYS)


def render_categorized_learnings(
    high_impact: list[dict[str, object]],
) -> str:
    """Render high-impact learnings categorized by tag type.

    Args:
        high_impact: List of high-impact learning entries.

    Returns:
        Markdown string with categorized learnings, or empty string.
    """
    if not high_impact:
        return ""
    categories: dict[str, list[str]] = {
        "Architecture": [],
        "Known Limitations": [],
        "Gotchas": [],
        "Key Learnings": [],
    }
    tag_to_category = {
        "architecture": "Architecture",
        "framework": "Architecture",
        "v17": "Architecture",
        "limitation": "Known Limitations",
        "improvement": "Known Limitations",
        "missing-tool": "Known Limitations",
        "gotcha": "Gotchas",
        "bug": "Gotchas",
        "configuration": "Gotchas",
    }
    for learning in high_impact[:CLAUDEMD_LEARNING_CAP]:
        summary = str(learning.get("summary", ""))
        tags = learning.get("tags", [])
        tag_list = tags if isinstance(tags, list) else []
        placed = False
        for tag in tag_list:
            cat = tag_to_category.get(str(tag))
            if cat:
                categories[cat].append(summary)
                placed = True
                break
        if not placed:
            categories["Key Learnings"].append(summary)

    lines: list[str] = []
    for cat_name, entries in categories.items():
        if entries:
            lines.append(f"### {cat_name}")
            for entry in entries:
                lines.append(f"- {entry}")
            lines.append("")
    if lines:
        return "\n".join(lines) + "\n"
    return ""


def render_patterns(patterns: list[dict[str, object]]) -> str:
    """Render discovered patterns to markdown.

    Args:
        patterns: List of pattern entries.

    Returns:
        Markdown string or empty string if no patterns.
    """
    if not patterns:
        return ""
    lines: list[str] = ["### Discovered Patterns"]
    for pattern in patterns[:CLAUDEMD_PATTERN_CAP]:
        name = pattern.get("name", "")
        desc = pattern.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    lines.append("")
    return "\n".join(lines) + "\n"


_ADHERENCE_TAGS = frozenset({
    "compliance", "process", "framework", "self-audit", "behavioral-mandate",
})
_ADHERENCE_KEYWORDS = ("must", "should", "call ", "never", "always")
_ADHERENCE_MAX_ENTRIES = 8
_ADHERENCE_MIN_LENGTH = 20


def render_adherence(high_impact: list[dict[str, object]]) -> str:
    """Render framework adherence directives from compliance learnings.

    Args:
        high_impact: List of high-impact learning entries.

    Returns:
        Markdown string with adherence directives, or empty string.
    """
    adherence_entries: list[str] = []
    for learning in high_impact:
        tags = learning.get("tags", [])
        tag_set = {str(t) for t in tags} if isinstance(tags, list) else set()
        if not (tag_set & _ADHERENCE_TAGS):
            continue

        # behavioral-mandate entries promote summary directly
        if "behavioral-mandate" in tag_set:
            summary = str(learning.get("summary", ""))
            if len(summary) > _ADHERENCE_MIN_LENGTH:
                adherence_entries.append(summary)
            continue

        detail = str(learning.get("detail", ""))
        for sentence in detail.split(". "):
            lower = sentence.lower()
            if any(kw in lower for kw in _ADHERENCE_KEYWORDS):
                clean = sentence.strip().rstrip(".")
                if len(clean) > _ADHERENCE_MIN_LENGTH:
                    adherence_entries.append(clean)

    if not adherence_entries:
        return ""

    # Deduplicate by prefix, capped at max entries
    lines: list[str] = ["### Framework Adherence"]
    seen: set[str] = set()
    for entry in adherence_entries:
        if len(seen) >= _ADHERENCE_MAX_ENTRIES:
            break
        key = entry[:60].lower()
        if key not in seen:
            lines.append(f"- {entry}")
            seen.add(key)
    lines.append("")
    return "\n".join(lines) + "\n"


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
        "(≤3 lines, 1 file). For everything else, delegate.\n"
        "\n"
        "TRW tools help you build effectively and preserve your work across sessions:\n"
        "- **Start**: call `trw_session_start()` to load prior learnings"
        " and recover any active run\n"
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
        "- `trw_session_start()` — loads your prior learnings and recovers any active run\n"
        "- `trw_checkpoint(message)` — saves progress so you can resume after context compaction\n"
        "- `trw_learn(summary, detail)` — records discoveries for all future sessions\n"
        "- `trw_deliver()` — persists everything in one call when done\n"
        "\n"
        "For full tool guide: invoke `/trw-ceremony-guide`\n"
        "\n"
        "Sessions where you orchestrate (delegate, verify, learn) "
        "rather than implement directly produce higher quality and "
        "fewer rework cycles — your strategic oversight is more "
        "valuable than your keystrokes.\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml.

    Returns:
        Markdown bullet list of directives, or empty string if file missing.
    """
    proto_path = (
        resolve_project_root() / _config.trw_dir / _config.context_dir / "behavioral_protocol.yaml"
    )
    if not proto_path.exists():
        return ""
    try:
        data = _reader.read_yaml(proto_path)
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
        " → ".join(phase_names),
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
        "Task arrives → Assess scope\n"
        "├── Trivial? (≤3 lines, 1 file) → Self-implement\n"
        "├── Research/read-only?          → Subagent (Explore/Plan type)\n"
        "├── Single-scope? (≤3 files)     → Subagent (general-purpose)\n"
        "├── Multi-scope? (4+ files)\n"
        "│   ├── Independent tracks?      → Batched subagents\n"
        "│   └── Interdependent?          → Agent Team\n"
        "└── Sprint-scale? (4+ PRDs)      → Agent Team + playbooks\n"
        "```\n"
        "\n"
        "**Default: subagents.** Use Agent Teams when teammates need peer communication "
        "or when tasks span 2+ modules with shared interfaces. As team lead, you "
        "orchestrate, monitor, and validate — teammates do the implementation.\n"
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
        "| You skip checkpoint → context compacts → you re-implement from scratch |\n"
        '| "I\'ll checkpoint/deliver after I finish this part" '
        "| Context compaction erases uncheckpointed work permanently "
        "| Past agents who skipped trw_deliver lost all session learnings |\n"
        '| "I already know the codebase" '
        "| Prior learnings contain gotchas for exactly this area "
        "| Sprint 26 had 6 P0/P1 defects from agents who skipped recall |\n"
        '| "I can implement directly, delegation is overhead" '
        "| Subagent implementation has 3x fewer P0 defects "
        "| Your focused context is valuable — subagents get deeper context per task |\n"
        '| "The build check can wait until the end" '
        "| Late build failures cascade into multi-file rework "
        "| 2x rework when caught at DELIVER vs catching at VALIDATE |\n"
        "\n"
        "### Rigid Tools (never skip, unconditional)\n"
        "\n"
        "- `trw_session_start()` — always, first action\n"
        "- `trw_deliver()` — always, last action\n"
        "- `trw_build_check()` — always at VALIDATE and DELIVER\n"
        "- Completion artifacts — always before marking task complete\n"
        "\n"
        "### Flexible Tools (must happen, you pick timing)\n"
        "\n"
        "- `trw_checkpoint()` — at milestones (you judge which)\n"
        "- `trw_learn()` — on discoveries/gotchas/errors\n"
        "- `trw_recall()` — recommended at start, skippable for repeat-domain\n"
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
        "should persist them at session end — this is how "
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
    if not _config.agent_teams_enabled:
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
        "- **TeammateIdle**: Fires when teammate goes idle — soft gate, logs for monitoring\n"
        "- **TaskCompleted**: Fires when task marked complete — extension point for validation\n"
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


def _truncate_with_markers(content_lines: list[str], max_lines: int) -> list[str]:
    """Truncate content while preserving TRW marker boundaries.

    QUAL-018: Finds the TRW start/end markers and truncates user content
    before them rather than cutting inside the auto-generated section.
    Falls back to simple truncation if markers are not intact.

    Args:
        content_lines: Lines of the CLAUDE.md file.
        max_lines: Maximum allowed line count.

    Returns:
        Truncated list of lines.
    """
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(content_lines):
        if TRW_MARKER_START in line:
            start_idx = i
        if TRW_MARKER_END in line:
            end_idx = i

    if start_idx is not None and end_idx is not None and end_idx < len(content_lines):
        user_lines = content_lines[:start_idx]
        trw_lines = content_lines[start_idx:end_idx + 1]
        after_lines = content_lines[end_idx + 1:]
        trw_size = len(trw_lines) + len(after_lines)
        user_budget = max(0, max_lines - trw_size - 1)
        truncated_user = user_lines[:user_budget]
        truncated_user.append("<!-- trw: user content truncated to line limit -->")
        return truncated_user + trw_lines + after_lines

    # No intact markers — fall back to simple truncation
    result = content_lines[:max_lines]
    result.append("<!-- trw: truncated to line limit -->")
    return result


def merge_trw_section(target: Path, trw_section: str, max_lines: int) -> int:
    """Merge TRW auto-generated section into a CLAUDE.md file.

    Preserves user-written content outside the TRW markers.
    Replaces existing TRW section if markers are present,
    otherwise appends.

    Args:
        target: Path to the CLAUDE.md file.
        trw_section: The generated TRW section markdown.
        max_lines: Maximum allowed lines in the output file.

    Returns:
        Total line count of the written file.
    """
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if TRW_MARKER_START in existing and TRW_MARKER_END in existing:
            cut_start = existing.index(TRW_MARKER_START)
            auto_idx = existing.rfind(TRW_AUTO_COMMENT, 0, cut_start)
            if auto_idx >= 0:
                cut_start = auto_idx
            before = existing[:cut_start].rstrip()
            after_marker = existing.index(TRW_MARKER_END) + len(TRW_MARKER_END)
            after = existing[after_marker:].lstrip("\n")
            new_content = before + trw_section + "\n" + after
        else:
            new_content = existing.rstrip() + "\n" + trw_section + "\n"
    else:
        new_content = trw_section.lstrip() + "\n"

    content_lines = new_content.split("\n")
    if len(content_lines) > max_lines:
        content_lines = _truncate_with_markers(content_lines, max_lines)
        new_content = "\n".join(content_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    _writer.write_text(target, new_content)
    return len(new_content.split("\n"))


def collect_promotable_learnings(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Collect active learnings eligible for CLAUDE.md promotion.

    Reads from SQLite via the memory adapter. For mature entries
    (q_observations >= threshold), q_value is used instead of static
    impact for the promotion decision (PRD-CORE-004 1c).

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance (kept for API compat).

    Returns:
        List of high-impact learning entry dicts.
    """
    high_impact: list[dict[str, object]] = []

    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        all_active = list_active_learnings(trw_dir)
    except Exception:
        return high_impact

    for data in all_active:
        try:
            impact = data.get("impact", 0.0)
            q_obs = int(str(data.get("q_observations", 0)))

            # Use q_value for mature entries, impact for cold-start
            if q_obs >= config.q_cold_start_threshold:
                score = float(str(data.get("q_value", impact)))
            else:
                score = float(str(impact)) if isinstance(impact, (int, float)) else 0.0

            # Apply time decay for accurate promotion decisions
            created_at_raw = str(data.get("created", ""))
            if created_at_raw:
                try:
                    from datetime import datetime as _dt

                    from trw_mcp.scoring import apply_time_decay
                    created_dt = _dt.fromisoformat(created_at_raw)
                    score = apply_time_decay(score, created_dt)
                except (ValueError, ImportError):
                    pass  # Malformed date — use raw score

            if score >= config.learning_promotion_impact:
                high_impact.append(data)
        except (ValueError, TypeError):
            continue

    return high_impact


def collect_patterns(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Collect pattern entries for CLAUDE.md sync.

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance.

    Returns:
        List of pattern entry dicts.
    """
    patterns: list[dict[str, object]] = []
    patterns_dir = trw_dir / config.patterns_dir
    if not patterns_dir.exists():
        return patterns

    for pattern_file in sorted(patterns_dir.glob("*.yaml")):
        if pattern_file.name == "index.yaml":
            continue
        try:
            patterns.append(reader.read_yaml(pattern_file))
        except (StateError, ValueError, TypeError):
            continue

    return patterns


def collect_context_data(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> tuple[dict[str, object], dict[str, object]]:
    """Collect architecture and conventions context data.

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance.

    Returns:
        Tuple of (architecture_data, conventions_data).
    """
    arch_data: dict[str, object] = {}
    conv_data: dict[str, object] = {}
    context_dir = trw_dir / config.context_dir
    try:
        if reader.exists(context_dir / "architecture.yaml"):
            arch_data = reader.read_yaml(context_dir / "architecture.yaml")
        if reader.exists(context_dir / "conventions.yaml"):
            conv_data = reader.read_yaml(context_dir / "conventions.yaml")
    except (StateError, ValueError, TypeError):
        pass
    return arch_data, conv_data


def execute_claude_md_sync(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
    llm: LLMClient,
) -> dict[str, object]:
    """Execute the CLAUDE.md sync operation.

    Core logic extracted from the ``trw_claude_md_sync`` tool to keep
    ``tools/learning.py`` under 400 lines (Sprint 12 GAP-FR-001).

    Args:
        scope: Sync scope -- "root" or "sub".
        target_dir: Target directory for sub-CLAUDE.md generation.
        config: TRW configuration.
        reader: File state reader.
        writer: File state writer.
        llm: LLM client instance.

    Returns:
        Result dictionary with sync metadata.
    """
    from trw_mcp.state.analytics import mark_promoted, update_analytics_sync
    from trw_mcp.state.llm_helpers import llm_summarize_learnings

    trw_dir = resolve_trw_dir()
    project_root = resolve_project_root()

    high_impact = collect_promotable_learnings(trw_dir, config, reader)
    patterns = collect_patterns(trw_dir, config, reader)
    _arch_data, _conv_data = collect_context_data(trw_dir, config, reader)

    llm_summary: str | None = None
    if (high_impact or patterns) and config.llm_enabled and llm.available:  # pragma: no cover
        llm_summary = llm_summarize_learnings(
            high_impact, patterns, llm, CLAUDEMD_LEARNING_CAP, CLAUDEMD_PATTERN_CAP,
        )

    template = load_claude_md_template(trw_dir)

    # PRD-CORE-061: Progressive disclosure — suppress ceremony/behavioral/learnings
    # sections from CLAUDE.md. These are now delivered via:
    # - /trw-ceremony-guide skill (on-demand)
    # - session-start.sh hook (behavioral protocol, one-time)
    # - trw_session_start() recall (learnings)
    tpl_context: dict[str, str] = {
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "closing_reminder": render_closing_reminder(),
        # Suppressed — moved to /trw-ceremony-guide skill
        "behavioral_protocol": "",
        "delegation_section": "",
        "agent_teams_section": "",
        "rationalization_watchlist": "",
        "ceremony_phases": "",
        "ceremony_table": "",
        "ceremony_flows": "",
        # Suppressed — learnings delivered via trw_session_start() recall
        "architecture_section": "",
        "conventions_section": "",
        "categorized_learnings": "",
        "patterns_section": "",
        "adherence_section": "",
    }

    trw_section = render_template(template, tpl_context)

    # PRD-CORE-061-FR04: Enforce max_auto_lines gate before writing
    auto_gen_lines = trw_section.count("\n")
    if auto_gen_lines > config.max_auto_lines:
        msg = (
            f"Auto-gen section is {auto_gen_lines} lines, "
            f"exceeds max_auto_lines={config.max_auto_lines}. "
            f"Refactor rendering before syncing."
        )
        raise StateError(msg)

    if scope == "sub" and target_dir:
        target = Path(target_dir).resolve() / "CLAUDE.md"
        max_lines = config.sub_claude_md_max_lines
    else:
        target = project_root / "CLAUDE.md"
        max_lines = config.claude_md_max_lines

    total_lines = merge_trw_section(target, trw_section, max_lines)
    update_analytics_sync(trw_dir)

    for learning in high_impact:
        lid = learning.get("id", "")
        if isinstance(lid, str) and lid:
            mark_promoted(trw_dir, lid)

    # PRD-INFRA-001: Sync AGENTS.md with same TRW section
    agents_md_synced = False
    agents_md_path: str | None = None
    if config.agents_md_enabled and scope == "root":
        agents_target = project_root / "AGENTS.md"
        merge_trw_section(agents_target, trw_section, max_lines)
        agents_md_synced = True
        agents_md_path = str(agents_target)

    logger.info(
        "trw_claude_md_synced", scope=scope, target=str(target),
        learnings_promoted=len(high_impact), patterns_included=len(patterns),
    )
    return {
        "path": str(target),
        "scope": scope,
        "status": "synced",
        "learnings_promoted": len(high_impact),
        "patterns_included": len(patterns),
        "total_lines": total_lines,
        "llm_used": llm_summary is not None,
        "agents_md_synced": agents_md_synced,
        "agents_md_path": agents_md_path,
        "bounded_contexts_synced": 0,
    }
