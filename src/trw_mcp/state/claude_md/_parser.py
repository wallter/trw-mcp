"""CLAUDE.md section extraction, marker parsing, template loading, and merge logic."""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateWriter

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
    config = get_config()

    # 1. Project-local override
    project_template = trw_dir / config.templates_dir / "claude_md.md"
    if project_template.exists():
        return project_template.read_text(encoding="utf-8")

    # 2. Bundled template
    data_dir = Path(__file__).parent.parent.parent / "data" / "templates"
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
        "{{framework_reference}}"
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

    Raises:
        StateError: If any ``{{placeholder}}`` tokens remain after replacement.
    """
    result = template
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", value)
    # Collapse runs of 3+ consecutive blank lines to 2
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    # Validate no unreplaced markers remain
    unreplaced = re.findall(r"\{\{(\w+)\}\}", result)
    if unreplaced:
        from trw_mcp.exceptions import StateError

        msg = f"Unresolved template markers: {', '.join(unreplaced)}"
        raise StateError(msg)
    return result


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
        trw_lines = content_lines[start_idx : end_idx + 1]
        after_lines = content_lines[end_idx + 1 :]
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

    writer = FileStateWriter()

    target.parent.mkdir(parents=True, exist_ok=True)
    writer.write_text(target, new_content)
    return len(new_content.split("\n"))
