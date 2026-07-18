"""INDEX.md and ROADMAP.md projection writer.

PRD-CORE-018: Scans PRD files, extracts frontmatter metadata,
and updates catalogue sections using marker-based merge to
preserve user-authored content outside the markers.

PRD-QUAL-121-FR03: this module is the sole INDEX/ROADMAP projection writer.
Executable (non-terminal) catalogue rows are rendered FROM the generated
requirements registry (``state/requirements_registry.py``) — the single
authority for executable PRD state — and each sync persists the canonical
registry document + receipt beside the scheduling ledger. Hand-edited
projection drift is detected by :func:`check_projection_drift`.

Also updates summary/header stats lines outside the markers so
they stay in sync with the auto-generated catalogue counts.
"""

from __future__ import annotations

# ruff: noqa: F401, I001 - private imports are compatibility re-exports.

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.state.requirements_registry import RegistryBuildResult

from trw_mcp.models.typed_dicts import RoadmapSyncResult, SyncIndexMdResult
from trw_mcp.state._index_sync_catalogue import (
    PRDEntry,
    _build_index_stats,
    _build_roadmap_stats,
    _group_by_status,
    _render_4col_table,
    _render_5col_table,
    _stats_parts,
)
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

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


# Tolerant line-level extractors for class-M (unparseable-frontmatter) PRDs.
# Mirrors scripts/check_prd_ids.py's regex fallback so the projection row and
# the identity gate agree on the SAME id/title for a file whose YAML fails.
_FALLBACK_ID_RE = re.compile(r"^\s*id:\s*['\"]?(PRD-[A-Z0-9-]+)['\"]?\s*$", re.MULTILINE)
_FALLBACK_TITLE_RE = re.compile(r"^\s*title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_FALLBACK_FIELD_RES = {
    "priority": re.compile(r"^\s*priority:\s*['\"]?([A-Za-z0-9]+)['\"]?\s*$", re.MULTILINE),
    "status": re.compile(r"^\s*status:\s*['\"]?([A-Za-z_-]+)['\"]?\s*$", re.MULTILINE),
    "category": re.compile(r"^\s*category:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$", re.MULTILINE),
}


def _fallback_entry(prd_file: Path, content: str) -> PRDEntry | None:
    """Best-effort catalogue row for a PRD whose frontmatter does not parse.

    Class-M files must stay VISIBLE in the generated projection — silently
    omitting them regressed 62+ pre-existing INDEX rows (2026-07-11). Values
    come from line-anchored regexes over the raw text, matching the identity
    gate's own fallback extraction, so no metadata is fabricated.
    """
    if not content.lstrip().startswith("---"):
        # Heading-only PRD (no frontmatter): same rule as check_prd_ids'
        # HEADING_RE owner extraction; prose **Status** line when present.
        heading = re.search(r"^#\s+(PRD-[A-Z0-9-]+):\s+(.+?)\s*$", content, re.MULTILINE)
        if heading is None:
            return None  # non-PRD markdown: never had a row
        status_match = re.search(r"^\*\*Status\*\*:\s*(\w+)", content, re.MULTILINE)
        return PRDEntry(
            id=heading.group(1),
            title=heading.group(2),
            priority="P1",
            status=(status_match.group(1).lower() if status_match else "draft"),
            category=heading.group(1).split("-")[1] if "-" in heading.group(1) else "",
        )
    id_match = _FALLBACK_ID_RE.search(content)
    title_match = _FALLBACK_TITLE_RE.search(content)
    fields = {
        name: (match.group(1) if (match := regex.search(content)) else "")
        for name, regex in _FALLBACK_FIELD_RES.items()
    }
    return PRDEntry(
        id=id_match.group(1) if id_match else prd_file.stem,
        title=title_match.group(1).strip() if title_match else "",
        priority=(fields["priority"] or "P1").upper(),
        status=(fields["status"] or "draft").lower(),
        category=fields["category"].upper(),
    )


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
                fallback = _fallback_entry(prd_file, content)
                if fallback is not None:
                    entries.append(fallback)
                continue

            prd_id = str(fm.get("id", prd_file.stem))
            title = str(fm.get("title", ""))
            priority = str(fm.get("priority", "P1")).upper()
            status = str(fm.get("status", "draft")).lower()
            category = str(fm.get("category", "")).upper()

            entries.append(
                PRDEntry(
                    id=prd_id,
                    title=title,
                    priority=priority,
                    status=status,
                    category=category,
                )
            )
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


def render_index_catalogue(entries: list[PRDEntry], registry: RegistryBuildResult | None = None) -> str:
    """Render the PRD catalogue section for INDEX.md.

    Args:
        entries: List of PRDEntry objects from scan_prd_frontmatters.
        registry: Optional RegistryBuildResult; when given, the
            registry-distinguishing executable block is rendered (FR03).

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
        f"## PRD Catalogue ({len(entries)} total: {', '.join(summary_parts)})",
        "",
    ]

    lines.extend(_render_registry_block(registry))
    lines.extend(_render_4col_table(f"### Done ({counts['done']})", groups["done"]))
    lines.extend(_render_4col_table(f"### Merged ({counts['merged']})", groups["merged"]))
    lines.extend(_render_5col_table(f"### Review / Groomed ({counts['review']})", groups["review"]))
    lines.extend(_render_4col_table(f"### Deprecated ({counts['deprecated']})", groups["deprecated"]))
    lines.extend(_render_4col_table(f"### Draft ({counts['draft']})", groups["draft"]))

    lines.append(INDEX_CATALOGUE_END)
    return "\n".join(lines)


def render_roadmap_catalogue(entries: list[PRDEntry], registry: RegistryBuildResult | None = None) -> str:
    """Render the PRD catalogue table for ROADMAP.md.

    Args:
        entries: List of PRDEntry objects from scan_prd_frontmatters.
        registry: Optional RegistryBuildResult; when given, the
            registry-distinguishing executable block is rendered (FR03).

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
        *_render_registry_block(registry),
        "| PRD | Title | Priority | Status | Category |",
        "|-----|-------|----------|--------|----------|",
    ]

    for e in sorted_entries:
        status_display = "**Done**" if e.status in _DONE_STATUSES else e.status.title()
        lines.append(
            f"| {e.id} | {e.title} | {e.priority} | {status_display} | {e.category} |",
        )

    lines.extend(["", ROADMAP_CATALOGUE_END])
    return "\n".join(lines)


def _find_marker_line(content: str, marker: str) -> tuple[int, int] | None:
    """Locate a marker that occupies a whole line, returning (start, end) offsets.

    Prose may *mention* a marker inline (e.g. inside backticks in a header
    sentence); matching the first raw substring occurrence truncated 705
    lines of ROADMAP.md planning body on 2026-06-11. Only a marker alone on
    its own line (modulo surrounding whitespace) delimits the section.
    """
    match = re.search(
        rf"^[ \t]*{re.escape(marker)}[ \t]*$",
        content,
        flags=re.MULTILINE,
    )
    if match is None:
        return None
    return match.start(), match.end()


def _merge_section(
    content: str,
    new_section: str,
    start_marker: str,
    end_marker: str,
) -> str:
    """Replace content between markers, or append if markers not found.

    Markers must occupy their own line to count — inline mentions of a
    marker (e.g. in documentation prose) are ignored.

    Args:
        content: Existing file content.
        new_section: New section content (includes markers).
        start_marker: Start marker string.
        end_marker: End marker string.

    Returns:
        Updated content with section replaced or appended.
    """
    start_span = _find_marker_line(content, start_marker)
    end_span = _find_marker_line(content, end_marker)
    if start_span is not None and end_span is not None and end_span[1] > start_span[0]:
        before = content[: start_span[0]].rstrip("\n")
        after = content[end_span[1] :].strip("\n")
        joiner = "\n\n" if before else ""
        # Normalize the tail to exactly one trailing newline: preserving the
        # tail's own trailing newlines and appending another grew the file by
        # one blank line per sync run (PRD-QUAL-122 idempotence regression).
        suffix = "\n\n" + after if after.strip() else ""
        return before + joiner + new_section + suffix + "\n"
    # No markers — append at end
    return content.rstrip() + "\n\n" + new_section + "\n"


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
    summary = _build_index_stats(groups, total) if index_format else _build_roadmap_stats(groups, total)
    match = pattern.search(content)
    if not match:
        return content
    prefix = match.group(1)
    return content[: match.start()] + prefix + summary + content[match.end() :]


def _find_trw_root(prds_dir: Path) -> Path | None:
    """Walk up from the corpus directory to the project root holding ``.trw``."""
    for parent in [prds_dir, *prds_dir.parents]:
        if (parent / ".trw").is_dir():
            return parent
    return None


def _apply_registry_authority(entries: list[PRDEntry], prds_dir: Path) -> RegistryBuildResult:
    """Reconcile the registry, overlay executable rows with its truth, persist it.

    The registry — not the raw frontmatter scan — is the authority for
    executable catalogue rows (PRD-QUAL-121-FR03). Terminal rows
    (done/merged/deprecated) are historical and stay source-scanned. The
    canonical registry document + receipt is persisted when a ``.trw`` root
    exists; hermetic corpora still route projections through the in-memory
    registry.

    Fail-closed: a stale, forked, or rolled-back scheduling ledger RAISES —
    a projection must never silently fall back to the pre-registry frontmatter
    authority (adversarial-audit finding 5, 2026-07-11).

    Returns the :class:`RegistryBuildResult` so renderers can project the
    registry-distinguishing state (execution state, hot path, expiry, epoch).
    """
    from trw_mcp.state.requirements_registry import (
        LEDGER_FILENAME,
        SchedulingLedgerError,
        build_registry,
        persist_registry,
    )

    trw_root = _find_trw_root(prds_dir)
    registry_dir = (trw_root / ".trw" / "registry") if trw_root else prds_dir / ".registry-scratch"
    registry = build_registry(prds_dir, registry_dir / LEDGER_FILENAME)
    if registry.status != "ok":
        raise SchedulingLedgerError(
            f"projection refused: scheduling ledger is {registry.status} ({registry.error}); "
            "prior projection remains intact"
        )
    if trw_root is not None:
        persist_registry(registry, registry_dir)

    by_id = {registry_entry.prd_id: registry_entry for registry_entry in registry.entries}
    for entry in entries:
        owner = by_id.get(entry.id)
        if owner is not None:
            entry.title = owner.title
            entry.priority = owner.priority
            entry.status = owner.lifecycle_status
            entry.category = owner.category
    return registry


def _render_registry_block(registry: RegistryBuildResult | None) -> list[str]:
    """Render the executable-registry section (PRD-QUAL-121-FR03).

    This block is derivable ONLY from the registry (scheduling ledger + epoch +
    receipt digest) — never from frontmatter — so the frontmatter scan alone can
    no longer reproduce the projection bytes. Bullet lists are used instead of
    tables so ``check_prd_ids`` catalogue-row parsing never mistakes execution
    state for a PRD title.
    """
    if registry is None or registry.epoch is None:
        return []
    lines = [
        f"### Executable Registry (epoch {registry.epoch.sequence} @ {registry.epoch.effective_utc_date})",
        "",
        f"- registry receipt: `{registry.receipt_digest()}`",
        f"- scheduling ledger head: `{registry.head_digest[:16]}`",
        f"- hot path: {len(registry.hot_path)} of {len(registry.entries)} executable",
    ]
    lines.extend(
        f"- {str(entry.execution_state).upper()}: {entry.prd_id} ({entry.owner})"
        for entry in registry.entries
        if str(entry.execution_state) in ("active", "blocked_external")
    )
    if registry.expired:
        lines.append("- expired (left hot path): " + ", ".join(registry.expired))
    lines.append("")
    return lines


def render_expected_projection(prds_dir: Path, *, kind: str) -> str:
    """Render the marker section a truthful sync would produce (drift oracle)."""
    entries = scan_prd_frontmatters(prds_dir)
    registry = _apply_registry_authority(entries, prds_dir)
    if kind == "index":
        return render_index_catalogue(entries, registry)
    return render_roadmap_catalogue(entries, registry)


def check_projection_drift(
    index_path: Path,
    roadmap_path: Path,
    prds_dir: Path,
) -> list[str]:
    """Detect hand-edited projection drift (PRD-QUAL-121-FR03 negative path).

    Compares the marker-delimited catalogue sections in INDEX.md/ROADMAP.md
    against freshly rendered registry projections. Any mismatch — including a
    missing marker section — is drift and fails the gate.
    """
    findings: list[str] = []
    for path, kind, start, end in (
        (index_path, "index", INDEX_CATALOGUE_START, INDEX_CATALOGUE_END),
        (roadmap_path, "roadmap", ROADMAP_CATALOGUE_START, ROADMAP_CATALOGUE_END),
    ):
        if not path.exists():
            findings.append(f"{kind}: projection file missing: {path}")
            continue
        content = path.read_text(encoding="utf-8")
        start_span = _find_marker_line(content, start)
        end_span = _find_marker_line(content, end)
        if start_span is None or end_span is None:
            findings.append(f"{kind}: catalogue markers missing in {path}")
            continue
        current = content[start_span[0] : end_span[1]]
        expected = render_expected_projection(prds_dir, kind=kind)
        if current.strip() != expected.strip():
            findings.append(f"{kind}: catalogue section drifted from registry projection in {path}")
    return findings


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
    registry = _apply_registry_authority(entries, prds_dir)
    groups = _group_by_status(entries)
    catalogue = render_index_catalogue(entries, registry)

    # Single read → merge catalogue + update header → single write
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        content = _merge_section(
            content,
            catalogue,
            INDEX_CATALOGUE_START,
            INDEX_CATALOGUE_END,
        )
    else:
        content = catalogue + "\n"
    content = _update_header_stats(
        content,
        groups,
        len(entries),
        _INDEX_SUMMARY_RE,
        index_format=True,
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
    registry = _apply_registry_authority(entries, prds_dir)
    groups = _group_by_status(entries)
    catalogue = render_roadmap_catalogue(entries, registry)

    # Single read → merge catalogue + update header → single write
    if roadmap_path.exists():
        content = roadmap_path.read_text(encoding="utf-8")
        content = _merge_section(
            content,
            catalogue,
            ROADMAP_CATALOGUE_START,
            ROADMAP_CATALOGUE_END,
        )
    else:
        content = catalogue + "\n"
    content = _update_header_stats(
        content,
        groups,
        len(entries),
        _ROADMAP_TOTAL_RE,
    )
    writer.write_text(roadmap_path, content)

    return {
        "roadmap_path": str(roadmap_path),
        "total_prds": len(entries),
    }
