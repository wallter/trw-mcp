"""CLAUDE.md template rendering — data-driven section builders and constants."""

from __future__ import annotations

from typing import NamedTuple

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
    CeremonyTool(
        "Start",
        "trw_session_start",
        "At session start \u2014 loads learnings + run state (pass query for focused recall)",
        "Recall learnings + check run status",
        "trw_session_start(query='task domain')",
    ),
    CeremonyTool(
        "Start",
        "trw_recall",
        "Quick tasks \u2014 retrieves relevant prior learnings",
        "Search learnings by query",
        "trw_recall('*', min_impact=0.7)",
    ),
    CeremonyTool(
        "Start",
        "trw_status",
        "When resuming \u2014 shows phase, progress, next steps",
        "Show run state and phase",
        "trw_status()",
    ),
    CeremonyTool(
        "RESEARCH",
        "trw_init",
        "New tasks \u2014 creates run directory for tracking",
        "Bootstrap run directory + events",
        "trw_init(task_name='...')",
    ),
    CeremonyTool(
        "Any",
        "trw_learn",
        "On errors/discoveries \u2014 saves for future sessions",
        "Record learning entry",
        "trw_learn(summary='...', impact=0.8)",
    ),
    CeremonyTool(
        "Any",
        "trw_checkpoint",
        "After milestones \u2014 preserves progress across compactions",
        "Atomic state snapshot",
        "trw_checkpoint(message='...')",
    ),
    CeremonyTool(
        "PLAN",
        "trw_prd_create",
        "When defining requirements",
        "Generate AARE-F PRD",
        "trw_prd_create(input_text='...')",
    ),
    CeremonyTool(
        "PLAN", "trw_prd_validate", "Before implementation", "PRD quality gate", "trw_prd_validate(prd_path='...')"
    ),
    CeremonyTool(
        "VALIDATE",
        "trw_build_check",
        "After implementation \u2014 runs tests and type-check, verifies integration",
        "Run tests + type-check",
        "trw_build_check(scope='full')",
    ),
    CeremonyTool(
        "REVIEW",
        "review diff",
        "After VALIDATE \u2014 check quality (DRY/KISS/SOLID), fix gaps, record learnings",
        "Review diff, fix incomplete integrations",
        "Read diff, fix gaps, trw_learn(summary='...')",
    ),
    CeremonyTool(
        "DELIVER",
        "trw_claude_md_sync",
        "At delivery \u2014 promotes learnings to CLAUDE.md",
        "Promote learnings to CLAUDE.md",
        "trw_claude_md_sync()",
    ),
    CeremonyTool(
        "DELIVER",
        "trw_deliver",
        "At task completion \u2014 persists everything in one call",
        "reflect+sync+checkpoint+index",
        "trw_deliver()",
    ),
]


_ARCH_SKIP_KEYS = frozenset({"notes"})
_CONV_SKIP_KEYS = frozenset({"notes", "test_patterns"})

_ADHERENCE_TAGS = frozenset(
    {
        "compliance",
        "process",
        "framework",
        "self-audit",
        "behavioral-mandate",
    }
)
_ADHERENCE_KEYWORDS = ("must", "should", "call ", "never", "always")
_ADHERENCE_MAX_ENTRIES = 8
_ADHERENCE_MIN_LENGTH = 20


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
            lines.extend(f"- {entry}" for entry in entries)
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
