"""CLAUDE.md section extraction, marker parsing, template loading, and merge logic."""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)

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

    Every ``{{word}}`` token present in *template* is replaced. Keys absent
    from *context* default to ``""`` so newer templates stay compatible with
    older ``trw-mcp`` installs (and vice versa) without failing sync.

    Args:
        template: Template string with ``{{key}}`` placeholders.
        context: Mapping of placeholder names to rendered content.

    Returns:
        Rendered markdown string with empty sections collapsed.

    Raises:
        StateError: If any ``{{placeholder}}`` tokens remain after replacement
            (e.g. a substituted value introduced a new ``{{...}}`` token).
    """
    keys_in_template = set(re.findall(r"\{\{(\w+)\}\}", template))
    merged: dict[str, str] = {key: context.get(key, "") for key in keys_in_template}
    result = template
    for key, value in merged.items():
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


def _marker_line_index(lines: list[str], marker: str, *, after: int | None = None) -> int | None:
    """Return the index of the first WHOLE LINE equal to *marker*, else ``None``.

    Marker matching MUST be line-anchored (strip the line, compare exactly), never
    a substring scan — see ``.claude/rules/trw-mcp-python.md`` §Marker / Sentinel
    Matching. A substring match once hit an inline prose mention of a sentinel and
    destroyed 705 ROADMAP lines; the same shape was live here, where
    ``existing.index(TRW_MARKER_START)`` resolved to a marker mentioned inside
    backticks and silently deleted every user line between that mention and the
    real block.

    *after*, when given, restricts the search to lines strictly after that index,
    so an end marker is always matched relative to its own start marker.
    """
    start = 0 if after is None else after + 1
    for i in range(start, len(lines)):
        if lines[i].strip() == marker:
            return i
    return None


def _duplicate_marker_lines(lines: list[str], marker: str) -> int:
    """Return how many WHOLE LINES equal *marker*."""
    return sum(1 for line in lines if line.strip() == marker)


def _block_cut_index(lines: list[str], start_idx: int) -> int:
    """Return where the TRW block really begins, including its auto-comment.

    Walks up from the start marker over blank lines only. The auto-comment counts
    as part of the block when it sits immediately above; anything else stops the
    walk, so user prose above the block is never absorbed into the replaced span.
    """
    cut = start_idx
    for j in range(start_idx - 1, -1, -1):
        stripped = lines[j].strip()
        if not stripped:
            continue
        if stripped == TRW_AUTO_COMMENT:
            cut = j
        break
    return cut


def _truncate_with_markers(
    content_lines: list[str],
    max_lines: int,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
) -> list[str]:
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
    # Line-anchored, like every other marker scan in this module (see
    # _marker_line_index); the previous ``marker in line`` form matched a marker
    # mentioned inside prose or backticks and mis-sited the truncation boundary.
    marker_start, marker_end = markers
    start_idx = _marker_line_index(content_lines, marker_start)
    end_idx = _marker_line_index(content_lines, marker_end, after=start_idx) if start_idx is not None else None

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


def merge_trw_section(
    target: Path,
    trw_section: str,
    max_lines: int,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
) -> int:
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
    # More than one well-formed block: we update the FIRST and the others go
    # stale. Refusing here is not better — the caller's malformed path appends,
    # which would add a third. But a silently frozen live block is exactly the
    # failure this module exists to prevent, so it is logged rather than hidden.
    # info, not debug: debug is dropped entirely under the shipped default level.
    _existing_lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    if _duplicate_marker_lines(_existing_lines, markers[0]) > 1:
        logger.info(
            "trw_block_duplicate_markers",
            path=str(target),
            starts=_duplicate_marker_lines(_existing_lines, markers[0]),
            note="only the first block is updated; the others will not track the framework",
        )

    # A blank-line separator is inserted before the TRW section whenever there is
    # preceding user content. ``before`` is rstripped and ``trw_section`` is
    # left-stripped of newlines, so the join can never glue the leading
    # ``<!-- TRW AUTO-GENERATED -->`` comment onto the end of a prose line — the
    # exact defect that corrupted AGENTS.md, whose section (unlike the CLAUDE.md
    # renderer's) does not start with a newline (PRD-QUAL-112).
    if target.exists():
        # PRD-CORE-203 FR04: never clobber a single-source pointer file (e.g. a
        # CLAUDE.md whose only substantive line is ``@AGENTS.md``). The shared
        # guard classifies the target, heals any stale appended block, and
        # signals skip — preventing both the append AND the marker-replace path
        # (which would otherwise re-clobber a pointer that carries a stale block).
        from trw_mcp.state.claude_md._instruction_carrier import pointer_skip_guard

        if pointer_skip_guard(target) is not None:
            return len(target.read_text(encoding="utf-8").split("\n"))
        existing = target.read_text(encoding="utf-8")
        # Line-anchored whole-line marker matching. The previous substring form
        # (``existing.index(TRW_MARKER_START)``) resolved to the FIRST occurrence
        # anywhere in the file — including a marker mentioned inside prose or
        # backticks — and then deleted everything from there to the real end
        # marker. That is the 705-line ROADMAP corruption shape, and it was live
        # on the delivery path: merge_trw_section is what trw_instructions_sync
        # and trw_deliver call for CLAUDE.md and AGENTS.md. The bootstrap sibling
        # (_template_claude_md.py) had already been hardened with
        # find_marker_line_span; this copy never was.
        existing_lines = existing.splitlines()
        marker_start, marker_end = markers
        start_idx = _marker_line_index(existing_lines, marker_start)
        end_idx = _marker_line_index(existing_lines, marker_end, after=start_idx) if start_idx is not None else None
        if start_idx is not None and end_idx is not None:
            cut = _block_cut_index(existing_lines, start_idx)
            before = "\n".join(existing_lines[:cut]).rstrip()
            after = "\n".join(existing_lines[end_idx + 1 :]).lstrip("\n")
            separator = "\n\n" if before else ""
            trailing = "\n" if after else ""
            new_content = before + separator + trw_section.lstrip("\n") + trailing + after
        else:
            before = existing.rstrip()
            separator = "\n\n" if before else ""
            new_content = before + separator + trw_section.lstrip("\n") + "\n"
    else:
        new_content = trw_section.lstrip() + "\n"

    content_lines = new_content.split("\n")
    if len(content_lines) > max_lines:
        content_lines = _truncate_with_markers(content_lines, max_lines, markers)
        new_content = "\n".join(content_lines)

    writer = FileStateWriter()

    target.parent.mkdir(parents=True, exist_ok=True)
    writer.write_text(target, new_content)
    return len(new_content.split("\n"))
