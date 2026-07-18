"""Catalogue data model and pure rendering helpers for index synchronization."""

from __future__ import annotations

from dataclasses import dataclass

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
    lines.extend(f"| {e.id} | {e.title} | {e.priority} | {e.category} |" for e in group)
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
    lines.extend(f"| {e.id} | {e.title} | {e.priority} | {e.status.title()} | {e.category} |" for e in group)
    lines.append("")
    return lines


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
