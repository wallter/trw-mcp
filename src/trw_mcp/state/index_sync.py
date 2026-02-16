"""INDEX.md and ROADMAP.md auto-sync from PRD frontmatter.

PRD-CORE-018: Scans PRD files, extracts frontmatter metadata,
and updates catalogue sections using marker-based merge to
preserve user-authored content outside the markers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

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


@dataclass
class PRDEntry:
    """Parsed PRD metadata for catalogue rendering."""

    id: str
    title: str
    priority: str
    status: str
    category: str
    sprint: str = "--"
    notes: str = ""


def scan_prd_frontmatters(prds_dir: Path) -> list[PRDEntry]:
    """Scan all PRD files and extract frontmatter metadata.

    Args:
        prds_dir: Directory containing PRD markdown files.

    Returns:
        Sorted list of PRDEntry objects.
    """
    entries: list[PRDEntry] = []
    if not prds_dir.exists():
        return entries

    for prd_file in sorted(prds_dir.glob("PRD-*.md")):
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
            continue

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


def render_index_catalogue(entries: list[PRDEntry]) -> str:
    """Render the PRD catalogue section for INDEX.md.

    Args:
        entries: List of PRDEntry objects from scan_prd_frontmatters.

    Returns:
        Markdown string with start/end markers for merge.
    """
    groups = _group_by_status(entries)
    done_count = len(groups["done"])
    merged_count = len(groups["merged"])
    review_count = len(groups["review"])
    draft_count = len(groups["draft"])
    deprecated_count = len(groups["deprecated"])

    summary_parts: list[str] = [f"{done_count} done"]
    if merged_count:
        summary_parts.append(f"{merged_count} merged")
    if deprecated_count:
        summary_parts.append(f"{deprecated_count} deprecated")
    if review_count:
        summary_parts.append(f"{review_count} review/groomed")
    summary_parts.append(f"{draft_count} draft")

    lines: list[str] = [
        INDEX_CATALOGUE_START,
        "",
        f"## PRD Catalogue ({len(entries)} total: "
        f"{', '.join(summary_parts)})",
        "",
    ]

    if groups["done"]:
        lines.append(f"### Done ({done_count})")
        lines.append("")
        lines.append("| PRD | Title | Priority | Category |")
        lines.append("|-----|-------|----------|----------|")
        for e in groups["done"]:
            lines.append(f"| {e.id} | {e.title} | {e.priority} | {e.category} |")
        lines.append("")

    if groups["merged"]:
        lines.append(f"### Merged ({merged_count})")
        lines.append("")
        lines.append("| PRD | Title | Priority | Category |")
        lines.append("|-----|-------|----------|----------|")
        for e in groups["merged"]:
            lines.append(f"| {e.id} | {e.title} | {e.priority} | {e.category} |")
        lines.append("")

    if groups["review"]:
        lines.append(f"### Review / Groomed ({review_count})")
        lines.append("")
        lines.append("| PRD | Title | Priority | Status | Category |")
        lines.append("|-----|-------|----------|--------|----------|")
        for e in groups["review"]:
            lines.append(
                f"| {e.id} | {e.title} | {e.priority} "
                f"| {e.status.title()} | {e.category} |",
            )
        lines.append("")

    if groups["deprecated"]:
        lines.append(f"### Deprecated ({deprecated_count})")
        lines.append("")
        lines.append("| PRD | Title | Priority | Category |")
        lines.append("|-----|-------|----------|----------|")
        for e in groups["deprecated"]:
            lines.append(f"| {e.id} | {e.title} | {e.priority} | {e.category} |")
        lines.append("")

    if groups["draft"]:
        lines.append(f"### Draft ({draft_count})")
        lines.append("")
        lines.append("| PRD | Title | Priority | Category |")
        lines.append("|-----|-------|----------|----------|")
        for e in groups["draft"]:
            lines.append(f"| {e.id} | {e.title} | {e.priority} | {e.category} |")
        lines.append("")

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

    lines.append("")
    lines.append(ROADMAP_CATALOGUE_END)
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


def sync_index_md(
    index_path: Path,
    prds_dir: Path,
    *,
    writer: FileStateWriter | None = None,
) -> dict[str, object]:
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
    catalogue = render_index_catalogue(entries)

    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        updated = _merge_section(
            content, catalogue,
            INDEX_CATALOGUE_START, INDEX_CATALOGUE_END,
        )
    else:
        updated = catalogue + "\n"

    writer.write_text(index_path, updated)

    groups = _group_by_status(entries)
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
) -> dict[str, object]:
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
    catalogue = render_roadmap_catalogue(entries)

    if roadmap_path.exists():
        content = roadmap_path.read_text(encoding="utf-8")
        updated = _merge_section(
            content, catalogue,
            ROADMAP_CATALOGUE_START, ROADMAP_CATALOGUE_END,
        )
    else:
        updated = catalogue + "\n"

    writer.write_text(roadmap_path, updated)

    return {
        "roadmap_path": str(roadmap_path),
        "total_prds": len(entries),
    }
