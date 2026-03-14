"""INDEX.md and ROADMAP.md auto-sync from PRD frontmatter.

PRD-CORE-018: Scans PRD files, extracts frontmatter metadata,
and updates catalogue sections using marker-based merge to
preserve user-authored content outside the markers.

Also updates summary/header stats lines outside the markers so
they stay in sync with the auto-generated catalogue counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import RoadmapSyncResult, SyncIndexMdResult
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger()

# Markers for auto-generated catalogue sections
INDEX_CATALOGUE_START = "<!-- trw:index-catalogue:start -->"
INDEX_CATALOGUE_END = "<!-- trw:index-catalogue:end -->"
ROADMAP_CATALOGUE_START = "<!-- trw:roadmap-catalogue:start -->"
ROADMAP_CATALOGUE_END = "<!-- trw:roadmap-catalogue:end -->"

_STATUS_ORDER: dict[str, int] = {
    "done": 0,
    "implemented": 0,
    "merged": 1,
    "approved": 2,
    "review": 3,
    "draft": 4,
    "deprecated": 5,
}

_DONE_STATUSES = frozenset({"done", "implemented"})
_REVIEW_STATUSES = frozenset({"review", "approved"})


@dataclass(slots=True)
class PRDEntry:
    """Parsed PRD metadata for catalogue rendering."""

    id: str
    title: str
    priority: str
    status: str
    category: str
    sprint: str = "--"
    notes: str = ""


def _scan_prd_dir(directory: Path) -> list[PRDEntry]:
    """Scan a single directory for PRD files and extract frontmatter metadata."""
    entries: list[PRDEntry] = []
    if not directory.exists():
        return entries

    for prd_file in sorted(directory.glob("PRD-*.md")):
        try:
            content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            if not fm:
                continue

            prd_id = str(fm.get("id", prd_file.stem))
            title = str(fm.get("title", ""))
            priority = str(fm.get("priority", "P1")).upper()
            status = str(fm.get("status", "draft")).lower()
            category = str(fm.get("category", "")).upper()

            entries.append(PRDEntry(
                id=prd_id,
                title=title,
                priority=priority,
                status=status,
                category=category,
            ))
        except (ValueError, TypeError, OSError) as exc:
            logger.debug("prd_scan_skip", file=str(prd_file), error=str(exc))

    return entries


def scan_prd_frontmatters(prds_dir: Path) -> list[PRDEntry]:
    """Scan all PRD files and extract frontmatter metadata.

    Scans both the active ``prds/`` directory and the sibling
    ``archive/prds/`` directory (if it exists) so that archived
    PRDs remain in the auto-generated catalogue.

    Args:
        prds_dir: Directory containing active PRD markdown files.

    Returns:
        Sorted list of PRDEntry objects (active + archived, deduplicated by ID).
    """
    entries = _scan_prd_dir(prds_dir)

    # Also scan archived PRDs (sibling archive/prds/ directory)
    archive_prds = prds_dir.parent / "archive" / "prds"
    archived = _scan_prd_dir(archive_prds)

    # Deduplicate: active PRDs take precedence over archived
    seen_ids = {e.id for e in entries}
    for entry in archived:
        if entry.id not in seen_ids:
            entries.append(entry)
            seen_ids.add(entry.id)

    return entries


def _group_by_status(
    entries: list[PRDEntry],
) -> dict[str, list[PRDEntry]]:
    """Group PRD entries by status category."""
    groups: dict[str, list[PRDEntry]] = {
        "done": [],
        "merged": [],
        "review": [],
        "draft": [],
        "deprecated": [],
    }
    for entry in entries:
        if entry.status in _DONE_STATUSES:
            groups["done"].append(entry)
        elif entry.status == "merged":
            groups["merged"].append(entry)
        elif entry.status == "deprecated":
            groups["deprecated"].append(entry)
        elif entry.status in _REVIEW_STATUSES:
            groups["review"].append(entry)
        else:
            groups["draft"].append(entry)
    return groups


def _render_4col_table(heading: str, group: list[PRDEntry]) -> list[str]:
    """Render a 4-column (PRD/Title/Priority/Category) table section.

    Returns empty list if group is empty.
    """
    if not group:
        return []
    lines = [
        heading,
        "",
        "| PRD | Title | Priority | Category |",
        "|-----|-------|----------|----------|",
    ]
    for e in group:
        lines.append(f"| {e.id} | {e.title} | {e.priority} | {e.category} |")
    lines.append("")
    return lines


def _render_5col_table(heading: str, group: list[PRDEntry]) -> list[str]:
    """Render a 5-column (PRD/Title/Priority/Status/Category) table section.

    Returns empty list if group is empty.
    """
    if not group:
        return []
    lines = [
        heading,
        "",
        "| PRD | Title | Priority | Status | Category |",
        "|-----|-------|----------|--------|----------|",
    ]
    for e in group:
        lines.append(
            f"| {e.id} | {e.title} | {e.priority} "
            f"| {e.status.title()} | {e.category} |",
        )
    lines.append("")
    return lines


def render_index_catalogue(entries: list[PRDEntry]) -> str:
    """Render the PRD catalogue section for INDEX.md.

    Args:
        entries: List of PRDEntry objects from scan_prd_frontmatters.

    Returns:
        Markdown string with start/end markers for merge.
    """
    groups = _group_by_status(entries)
    counts = {key: len(items) for key, items in groups.items()}

    summary_parts: list[str] = [f"{counts['done']} done"]
    if counts["merged"]:
        summary_parts.append(f"{counts['merged']} merged")
    if counts["deprecated"]:
        summary_parts.append(f"{counts['deprecated']} deprecated")
    if counts["review"]:
        summary_parts.append(f"{counts['review']} review/groomed")
    summary_parts.append(f"{counts['draft']} draft")

    lines: list[str] = [
        INDEX_CATALOGUE_START,
        "",
        f"## PRD Catalogue ({len(entries)} total: "
        f"{', '.join(summary_parts)})",
        "",
    ]

    lines.extend(_render_4col_table(f"### Done ({counts['done']})", groups["done"]))
    lines.extend(_render_4col_table(f"### Merged ({counts['merged']})", groups["merged"]))
    lines.extend(_render_5col_table(f"### Review / Groomed ({counts['review']})", groups["review"]))
    lines.extend(_render_4col_table(f"### Deprecated ({counts['deprecated']})", groups["deprecated"]))
    lines.extend(_render_4col_table(f"### Draft ({counts['draft']})", groups["draft"]))

    lines.append(INDEX_CATALOGUE_END)
    return "\n".join(lines)


def render_roadmap_catalogue(entries: list[PRDEntry]) -> str:
    """Render the PRD catalogue table for ROADMAP.md.

    Args:
        entries: List of PRDEntry objects from scan_prd_frontmatters.

    Returns:
        Markdown string with start/end markers for merge.
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: (_STATUS_ORDER.get(e.status, 99), e.id),
    )

    lines: list[str] = [
        ROADMAP_CATALOGUE_START,
        "",
        f"## PRD Catalogue ({len(entries)} total)",
        "",
        "| PRD | Title | Priority | Status | Category |",
        "|-----|-------|----------|--------|----------|",
    ]

    for e in sorted_entries:
        status_display = "**Done**" if e.status in _DONE_STATUSES else e.status.title()
        lines.append(
            f"| {e.id} | {e.title} | {e.priority} "
            f"| {status_display} | {e.category} |",
        )

    lines.extend(["", ROADMAP_CATALOGUE_END])
    return "\n".join(lines)


def _merge_section(
    content: str,
    new_section: str,
    start_marker: str,
    end_marker: str,
) -> str:
    """Replace content between markers, or append if markers not found.

    Args:
        content: Existing file content.
        new_section: New section content (includes markers).
        start_marker: Start marker string.
        end_marker: End marker string.

    Returns:
        Updated content with section replaced or appended.
    """
    if start_marker in content and end_marker in content:
        start_idx = content.index(start_marker)
        end_idx = content.index(end_marker) + len(end_marker)
        before = content[:start_idx].rstrip("\n")
        after = content[end_idx:].lstrip("\n")
        joiner = "\n\n" if before else ""
        suffix = "\n\n" + after if after.strip() else ""
        return before + joiner + new_section + suffix + "\n"
    # No markers — append at end
    return content.rstrip() + "\n\n" + new_section + "\n"


def _stats_parts(groups: dict[str, list[PRDEntry]]) -> list[str]:
    """Build the comma-separated status count fragments."""
    parts: list[str] = [f"{len(groups['done'])} done"]
    if groups["merged"]:
        parts.append(f"{len(groups['merged'])} merged")
    if groups["deprecated"]:
        parts.append(f"{len(groups['deprecated'])} deprecated")
    if groups["review"]:
        parts.append(f"{len(groups['review'])} review")
    parts.append(f"{len(groups['draft'])} draft")
    return parts


def _build_index_stats(groups: dict[str, list[PRDEntry]], total: int) -> str:
    """Build INDEX.md format: ``(N total: X done, Y draft)``."""
    return f"({total} total: {', '.join(_stats_parts(groups))})"


def _build_roadmap_stats(groups: dict[str, list[PRDEntry]], total: int) -> str:
    """Build ROADMAP.md format: ``N (X done, Y draft)``."""
    return f"{total} ({', '.join(_stats_parts(groups))})"


# Patterns for header stats outside the catalogue markers.
# INDEX.md: ## Summary (N total: N done, ...)
_INDEX_SUMMARY_RE = re.compile(
    r"^(## Summary )\(\d+ total:.*?\)$",
    re.MULTILINE,
)
# ROADMAP.md: **PRD Total**: N (N done, ...)
_ROADMAP_TOTAL_RE = re.compile(
    r"^(\*\*PRD Total\*\*: )\d+ \(.*?\)$",
    re.MULTILINE,
)


def _update_header_stats(
    content: str,
    groups: dict[str, list[PRDEntry]],
    total: int,
    pattern: re.Pattern[str],
    *,
    index_format: bool = False,
) -> str:
    """Update a stats header line outside the catalogue markers.

    Finds the line matching *pattern* and replaces the stats portion
    with current counts. Returns *content* unchanged if no match.

    Args:
        content: File content.
        groups: PRD status groups.
        total: Total PRD count.
        pattern: Compiled regex with a capture group for the line prefix.
        index_format: Use INDEX.md ``(N total: ...)`` format when True,
            ROADMAP.md ``N (...)`` format when False.
    """
    summary = (
        _build_index_stats(groups, total)
        if index_format
        else _build_roadmap_stats(groups, total)
    )
    match = pattern.search(content)
    if not match:
        return content
    prefix = match.group(1)
    return content[:match.start()] + prefix + summary + content[match.end():]



def sync_index_md(
    index_path: Path,
    prds_dir: Path,
    *,
    writer: FileStateWriter | None = None,
) -> SyncIndexMdResult:
    """Sync INDEX.md PRD catalogue from PRD frontmatter.

    Reads all PRD files, groups by status, and updates the
    catalogue section between markers. Content outside markers
    is preserved.

    Args:
        index_path: Path to INDEX.md file.
        prds_dir: Directory containing PRD files.
        writer: Optional writer for atomic writes.

    Returns:
        Dict with sync results (counts per status group).
    """
    writer = writer or FileStateWriter()
    entries = scan_prd_frontmatters(prds_dir)
    groups = _group_by_status(entries)
    catalogue = render_index_catalogue(entries)

    # Single read → merge catalogue + update header → single write
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        content = _merge_section(
            content, catalogue, INDEX_CATALOGUE_START, INDEX_CATALOGUE_END,
        )
    else:
        content = catalogue + "\n"
    content = _update_header_stats(
        content, groups, len(entries), _INDEX_SUMMARY_RE, index_format=True,
    )
    writer.write_text(index_path, content)

    return {
        "index_path": str(index_path),
        "total_prds": len(entries),
        "done": len(groups["done"]),
        "merged": len(groups["merged"]),
        "review": len(groups["review"]),
        "deprecated": len(groups["deprecated"]),
        "draft": len(groups["draft"]),
    }


def sync_roadmap_md(
    roadmap_path: Path,
    prds_dir: Path,
    *,
    writer: FileStateWriter | None = None,
) -> RoadmapSyncResult:
    """Sync ROADMAP.md PRD catalogue table from PRD frontmatter.

    Reads all PRD files and updates the catalogue table between
    markers. Sprint details and other content outside markers
    is preserved.

    Args:
        roadmap_path: Path to ROADMAP.md file.
        prds_dir: Directory containing PRD files.
        writer: Optional writer for atomic writes.

    Returns:
        Dict with sync results.
    """
    writer = writer or FileStateWriter()
    entries = scan_prd_frontmatters(prds_dir)
    groups = _group_by_status(entries)
    catalogue = render_roadmap_catalogue(entries)

    # Single read → merge catalogue + update header → single write
    if roadmap_path.exists():
        content = roadmap_path.read_text(encoding="utf-8")
        content = _merge_section(
            content, catalogue, ROADMAP_CATALOGUE_START, ROADMAP_CATALOGUE_END,
        )
    else:
        content = catalogue + "\n"
    content = _update_header_stats(
        content, groups, len(entries), _ROADMAP_TOTAL_RE,
    )
    writer.write_text(roadmap_path, content)

    return {
        "roadmap_path": str(roadmap_path),
        "total_prds": len(entries),
    }
