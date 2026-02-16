"""CLAUDE.md rendering and sync — template loading, section generation, marker-based merge."""

from __future__ import annotations

import structlog

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

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
    ("IMPLEMENT", "Execute work with periodic checkpoints"),
    ("VALIDATE", "Run tests, build checks, verify correctness"),
    ("REVIEW", "Reflect on learnings, check compliance"),
    ("DELIVER", "Sync artifacts, checkpoint, close run"),
]

# 11 lifecycle-critical tools in execution order
CEREMONY_TOOLS: list[CeremonyTool] = [
    CeremonyTool("Start", "trw_session_start", "ALWAYS at session start", "Recall learnings + check run status", "trw_session_start()"),
    CeremonyTool("Start", "trw_recall", "ALWAYS for quick tasks (no run)", "Search learnings by query", "trw_recall('*', min_impact=0.7)"),
    CeremonyTool("Start", "trw_status", "ALWAYS when resuming a run", "Show run state and phase", "trw_status()"),
    CeremonyTool("RESEARCH", "trw_init", "ALWAYS for new tasks", "Bootstrap run directory + events", "trw_init(task_name='...')"),
    CeremonyTool("Any", "trw_learn", "ALWAYS on errors/discoveries", "Record learning entry", "trw_learn(summary='...', impact=0.8)"),
    CeremonyTool("Any", "trw_checkpoint", "Every milestone / ~10min", "Atomic state snapshot", "trw_checkpoint(message='...')"),
    CeremonyTool("PLAN", "trw_prd_create", "When defining requirements", "Generate AARE-F PRD", "trw_prd_create(input_text='...')"),
    CeremonyTool("PLAN", "trw_prd_validate", "Before implementation", "PRD quality gate", "trw_prd_validate(prd_path='...')"),
    CeremonyTool("VALIDATE", "trw_build_check", "ALWAYS before delivery", "Run pytest + mypy", "trw_build_check(scope='full')"),
    CeremonyTool("DELIVER", "trw_claude_md_sync", "ALWAYS at delivery", "Promote learnings to CLAUDE.md", "trw_claude_md_sync()"),
    CeremonyTool("DELIVER", "trw_deliver", "ALWAYS at task completion", "reflect+sync+checkpoint+index", "trw_deliver()"),
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
        "## TRW Behavioral Protocol (Auto-Generated)\n"
        "\n"
        "{{behavioral_protocol}}"
        "## TRW Ceremony Tools (Auto-Generated)\n"
        "\n"
        "{{ceremony_phases}}"
        "{{ceremony_table}}"
        "{{ceremony_flows}}"
        "## TRW Learnings (Auto-Generated)\n"
        "\n"
        "{{architecture_section}}"
        "{{conventions_section}}"
        "{{categorized_learnings}}"
        "{{patterns_section}}"
        "{{adherence_section}}"
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


def render_architecture(arch_data: dict[str, object]) -> str:
    """Render architecture context to markdown.

    Args:
        arch_data: Architecture data from context/architecture.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    if not arch_data:
        return ""
    lines: list[str] = ["### Architecture"]
    for key, val in arch_data.items():
        if val and key != "notes":
            lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_conventions(conv_data: dict[str, object]) -> str:
    """Render conventions context to markdown.

    Args:
        conv_data: Conventions data from context/conventions.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    if not conv_data:
        return ""
    lines: list[str] = ["### Conventions"]
    for key, val in conv_data.items():
        if val and key not in ("notes", "test_patterns"):
            lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines) + "\n"


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


def render_adherence(high_impact: list[dict[str, object]]) -> str:
    """Render framework adherence directives from compliance learnings.

    Args:
        high_impact: List of high-impact learning entries.

    Returns:
        Markdown string with adherence directives, or empty string.
    """
    _adherence_tags = {"compliance", "process", "framework", "self-audit", "behavioral-mandate"}
    adherence_entries: list[str] = []
    for learning in high_impact:
        tags = learning.get("tags", [])
        tag_set = {str(t) for t in tags} if isinstance(tags, list) else set()
        if tag_set & _adherence_tags:
            # behavioral-mandate entries promote summary directly
            if "behavioral-mandate" in tag_set:
                summary = str(learning.get("summary", ""))
                if summary and len(summary) > 20:
                    adherence_entries.append(summary)
                continue
            detail = str(learning.get("detail", ""))
            for sentence in detail.split(". "):
                lower = sentence.lower()
                if any(kw in lower for kw in ("must", "should", "call ", "never", "always")):
                    clean = sentence.strip().rstrip(".")
                    if clean and len(clean) > 20:
                        adherence_entries.append(clean)

    if not adherence_entries:
        return ""
    lines: list[str] = ["### Framework Adherence"]
    seen: set[str] = set()
    count = 0
    for entry in adherence_entries:
        key = entry[:60].lower()
        if key not in seen and count < 8:
            lines.append(f"- {entry}")
            seen.add(key)
            count += 1
    lines.append("")
    return "\n".join(lines) + "\n"


def render_imperative_opener() -> str:
    """Render the high-salience imperative opener for the TRW auto-generated section.

    This MUST appear at the very top of the auto-generated block so agents
    see it before anything else. It provides the minimum viable ceremony
    trigger that drives tool adoption.

    Returns:
        Markdown string with imperative instructions.
    """
    return (
        "CRITICAL — YOU MUST EXECUTE THESE TOOLS:\n"
        "- **BEFORE ANY WORK**: ALWAYS call `trw_session_start()` "
        "(or `trw_recall('*', min_impact=0.7)` for quick tasks). NEVER skip this step.\n"
        "- **AFTER COMPLETING WORK**: ALWAYS call `trw_deliver()` "
        "(or `trw_claude_md_sync` for quick tasks). NEVER skip this step.\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml.

    Returns:
        Markdown bullet list of directives, or empty string if file missing.
    """
    proto_path = resolve_project_root() / _config.trw_dir / _config.context_dir / "behavioral_protocol.yaml"
    if not proto_path.exists():
        return ""
    try:
        data = _reader.read_yaml(proto_path)
    except (StateError, ValueError, TypeError):
        return ""
    directives = data.get("directives", [])
    if not directives or not isinstance(directives, list):
        return ""
    lines: list[str] = []
    for directive in directives[:BEHAVIORAL_PROTOCOL_CAP]:
        lines.append(f"- {directive}")
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
        "  -> trw_build_check(scope='full')\n"
        "  -> trw_deliver()\n"
        "```\n"
        "\n"
    )


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
        content_lines = content_lines[:max_lines]
        content_lines.append("<!-- trw: truncated to line limit -->")
        new_content = "\n".join(content_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    _writer.write_text(target, new_content)
    return len(new_content.split("\n"))


def collect_promotable_learnings(
    trw_dir: Path,
    config: "TRWConfig",
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Collect active learnings eligible for CLAUDE.md promotion.

    For mature entries (q_observations >= threshold), q_value is used
    instead of static impact for the promotion decision (PRD-CORE-004 1c).

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance.

    Returns:
        List of high-impact learning entry dicts.
    """
    from trw_mcp.exceptions import StateError as _StateError

    high_impact: list[dict[str, object]] = []
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    if not entries_dir.exists():
        return high_impact

    for entry_file in sorted(entries_dir.glob("*.yaml")):
        try:
            data = reader.read_yaml(entry_file)
            entry_status = str(data.get("status", "active"))
            if entry_status != "active":
                continue

            impact = data.get("impact", 0.0)
            q_obs = int(str(data.get("q_observations", 0)))
            q_val = data.get("q_value", impact)

            # Use q_value for mature entries, impact for cold-start
            if q_obs >= config.q_cold_start_threshold:
                score = float(str(q_val))
            else:
                score = float(str(impact)) if isinstance(impact, (int, float)) else 0.0

            if score >= config.learning_promotion_impact:
                high_impact.append(data)
        except (_StateError, ValueError, TypeError):
            continue

    return high_impact


def collect_patterns(
    trw_dir: Path,
    config: "TRWConfig",
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
    from trw_mcp.exceptions import StateError as _StateError

    patterns: list[dict[str, object]] = []
    patterns_dir = trw_dir / config.patterns_dir
    if not patterns_dir.exists():
        return patterns

    for pattern_file in sorted(patterns_dir.glob("*.yaml")):
        if pattern_file.name == "index.yaml":
            continue
        try:
            patterns.append(reader.read_yaml(pattern_file))
        except (_StateError, ValueError, TypeError):
            continue

    return patterns


def collect_context_data(
    trw_dir: Path,
    config: "TRWConfig",
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
    from trw_mcp.exceptions import StateError as _StateError

    arch_data: dict[str, object] = {}
    conv_data: dict[str, object] = {}
    context_dir = trw_dir / config.context_dir
    try:
        if reader.exists(context_dir / "architecture.yaml"):
            arch_data = reader.read_yaml(context_dir / "architecture.yaml")
        if reader.exists(context_dir / "conventions.yaml"):
            conv_data = reader.read_yaml(context_dir / "conventions.yaml")
    except (_StateError, ValueError, TypeError):
        pass
    return arch_data, conv_data


def execute_claude_md_sync(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
    llm: "LLMClient",
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
    arch_data, conv_data = collect_context_data(trw_dir, config, reader)

    llm_used = False
    llm_summary: str | None = None
    if (high_impact or patterns) and config.llm_enabled and llm.available:  # pragma: no cover
        llm_summary = llm_summarize_learnings(
            high_impact, patterns, llm, CLAUDEMD_LEARNING_CAP, CLAUDEMD_PATTERN_CAP,
        )
        if llm_summary is not None:
            llm_used = True

    template = load_claude_md_template(trw_dir)
    imperative_opener = render_imperative_opener()
    behavioral_protocol = render_behavioral_protocol()
    ceremony_phases = render_phase_descriptions()
    ceremony_table = render_ceremony_table()
    ceremony_flows = render_ceremony_flows()

    if llm_used and llm_summary is not None:
        tpl_context: dict[str, str] = {
            "imperative_opener": imperative_opener,
            "behavioral_protocol": behavioral_protocol,
            "ceremony_phases": ceremony_phases,
            "ceremony_table": ceremony_table,
            "ceremony_flows": ceremony_flows,
            "architecture_section": "",
            "conventions_section": "",
            "categorized_learnings": llm_summary + "\n",
            "patterns_section": "",
            "adherence_section": "",
        }
    else:
        tpl_context = {
            "imperative_opener": imperative_opener,
            "behavioral_protocol": behavioral_protocol,
            "ceremony_phases": ceremony_phases,
            "ceremony_table": ceremony_table,
            "ceremony_flows": ceremony_flows,
            "architecture_section": render_architecture(arch_data),
            "conventions_section": render_conventions(conv_data),
            "categorized_learnings": render_categorized_learnings(high_impact),
            "patterns_section": render_patterns(patterns),
            "adherence_section": render_adherence(high_impact),
        }

    trw_section = render_template(template, tpl_context)

    bounded_context_count = 0

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
        "path": str(target), "scope": scope,
        "learnings_promoted": len(high_impact),
        "patterns_included": len(patterns),
        "total_lines": total_lines, "status": "synced", "llm_used": llm_used,
        "agents_md_synced": agents_md_synced,
        "agents_md_path": agents_md_path,
        "bounded_contexts_synced": bounded_context_count,
    }
