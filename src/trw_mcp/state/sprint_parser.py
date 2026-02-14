"""Sprint document parser — extracts structured data from sprint markdown.

Pure parsing logic. No I/O beyond the content string passed in.
Follows the same pattern as prd_utils.py and ears_classifier.py.
"""

from __future__ import annotations

import re

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.sprint import FileOverlapEntry, SprintDoc, SprintTrack

# --- Regex patterns ---

# Sprint number from title or frontmatter: "# Sprint 11: ..."
_SPRINT_NUMBER_RE = re.compile(r"#\s+Sprint\s+(\d+)", re.IGNORECASE)

# Track heading: "## Track A: FRAMEWORK.md v20.0 Rewrite"
_TRACK_HEADING_RE = re.compile(r"^##\s+Track\s+([A-Z]):\s*(.*)$", re.MULTILINE)

# PRD references: PRD-CORE-013, PRD-FIX-010, etc.
_PRD_REF_RE = re.compile(r"PRD-[A-Z]+-\d{3}")

# Checkbox items: "- [ ] foo" or "- [x] bar"
_CHECKBOX_RE = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)

# Goal line: "**Goal**: ..." (bold prefix)
_GOAL_RE = re.compile(r"\*\*Goal\*\*:\s*(.+?)(?:\n|$)")

# File overlap matrix row: "| `path` | WRITE | -- | -- | ..."
_OVERLAP_ROW_RE = re.compile(
    r"^\|\s*`?([^|`]+?)`?\s*\|(.+)$", re.MULTILINE,
)

# Merge order line
_MERGE_ORDER_RE = re.compile(
    r"\*\*Merge order\*\*:\s*(.+?)(?:\n|$)", re.IGNORECASE,
)

# Backtick-quoted file paths in text
_BACKTICK_PATH_RE = re.compile(r"`([^`]*(?:\.(?:py|md|yaml|yml|json|toml))[^`]*)`")

# Section heading for "### Files Modified" or "### Files"
_FILES_SECTION_RE = re.compile(
    r"^###\s+Files\s*(?:Modified)?\s*$", re.MULTILINE | re.IGNORECASE,
)

# Section heading for "### Validation"
_VALIDATION_SECTION_RE = re.compile(
    r"^###\s+Validation\s*$", re.MULTILINE | re.IGNORECASE,
)

# Definition of Done heading
_DOD_HEADING_RE = re.compile(
    r"^##\s+Definition\s+of\s+Done", re.MULTILINE | re.IGNORECASE,
)

# Track sub-DoD heading: "### Track A"
_TRACK_DOD_RE = re.compile(r"^###\s+Track\s+([A-Z])\s*$", re.MULTILINE)


def extract_prd_refs(text: str) -> list[str]:
    """Extract unique PRD references from text.

    Args:
        text: Markdown text to scan.

    Returns:
        Sorted list of unique PRD IDs found.
    """
    refs = _PRD_REF_RE.findall(text)
    return sorted(set(refs))


def extract_dod_items(content: str) -> list[str]:
    """Extract Definition of Done checkbox items from content.

    Args:
        content: Markdown text (may be full doc or section).

    Returns:
        List of checkbox item texts (with status prefix).
    """
    items: list[str] = []
    for match in _CHECKBOX_RE.finditer(content):
        checked = match.group(1).lower() == "x"
        text = match.group(2).strip()
        prefix = "[x]" if checked else "[ ]"
        items.append(f"{prefix} {text}")
    return items


def _extract_section_text(content: str, start_pattern: re.Pattern[str]) -> str:
    """Extract text from a section heading to the next heading of same or higher level.

    Args:
        content: Full document content.
        start_pattern: Compiled regex for the section heading.

    Returns:
        Section text (excluding the heading itself), or empty string.
    """
    match = start_pattern.search(content)
    if not match:
        return ""
    start = match.end()
    # Find next heading at same or higher level
    heading_level = content[match.start():match.end()].count("#", 0, 4)
    next_heading = re.compile(
        rf"^{'#' * heading_level}(?!#)\s", re.MULTILINE,
    )
    end_match = next_heading.search(content, start)
    end = end_match.start() if end_match else len(content)
    return content[start:end]


def _extract_files_from_section(section_text: str) -> list[str]:
    """Extract file paths from a "Files Modified" section.

    Looks for backtick-quoted paths and bare bullet paths.

    Args:
        section_text: Text of the Files Modified section.

    Returns:
        List of file paths found.
    """
    files: list[str] = []
    for match in _BACKTICK_PATH_RE.finditer(section_text):
        path = match.group(1).strip()
        if path and not path.startswith("#"):
            files.append(path)
    # Also pick up "- path/to/file.py" style bullets
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            path_candidate = stripped[2:].strip().strip("`")
            if "/" in path_candidate and "." in path_candidate:
                # Remove trailing description after " — " or " - "
                for sep in (" — ", " - ", " – "):
                    if sep in path_candidate:
                        path_candidate = path_candidate[:path_candidate.index(sep)]
                        break
                path_candidate = path_candidate.strip()
                if path_candidate and path_candidate not in files:
                    files.append(path_candidate)
    return files


def _extract_validation_criteria(section_text: str) -> list[str]:
    """Extract validation checkbox items from a Validation section.

    Args:
        section_text: Text of the Validation section.

    Returns:
        List of validation criteria texts.
    """
    return extract_dod_items(section_text)


def extract_tracks(content: str) -> list[SprintTrack]:
    """Extract track definitions from sprint document content.

    Splits on ``## Track [A-Z]: ...`` headings and parses each track's
    PRD scope, files, validation criteria, and DoD items.

    Args:
        content: Full sprint document markdown.

    Returns:
        List of SprintTrack models.
    """
    headings = list(_TRACK_HEADING_RE.finditer(content))
    if not headings:
        return []

    tracks: list[SprintTrack] = []

    for i, heading in enumerate(headings):
        letter = heading.group(1)
        title = heading.group(2).strip()
        start = heading.end()
        # End at next ## heading or end of content
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        # But also stop at ## headings that aren't track headings
        next_h2 = re.search(r"^##\s+(?!Track\s+[A-Z]:)", content[start:end], re.MULTILINE)
        if next_h2:
            end = start + next_h2.start()

        section = content[start:end]

        # Extract PRD refs from track section
        prd_scope = extract_prd_refs(section)

        # Extract files from ### Files Modified section
        files: list[str] = []
        files_match = _FILES_SECTION_RE.search(section)
        if files_match:
            files_section_start = files_match.end()
            # Find next ### heading
            next_subsection = re.search(r"^###\s+", section[files_section_start:], re.MULTILINE)
            files_section_end = (
                files_section_start + next_subsection.start()
                if next_subsection
                else len(section)
            )
            files_text = section[files_section_start:files_section_end]
            files = _extract_files_from_section(files_text)

        # Fallback: extract backtick paths from entire track section
        if not files:
            files = _extract_files_from_section(section)

        # Extract validation criteria
        validation_criteria: list[str] = []
        validation_match = _VALIDATION_SECTION_RE.search(section)
        if validation_match:
            val_start = validation_match.end()
            next_subsection = re.search(r"^###\s+", section[val_start:], re.MULTILINE)
            val_end = val_start + next_subsection.start() if next_subsection else len(section)
            val_text = section[val_start:val_end]
            validation_criteria = _extract_validation_criteria(val_text)

        # Extract per-track DoD items (from ## Definition of Done → ### Track X)
        dod_items: list[str] = []
        dod_match = _DOD_HEADING_RE.search(content)
        if dod_match:
            dod_section_start = dod_match.end()
            # Find ### Track {letter} within DoD
            for track_dod_match in _TRACK_DOD_RE.finditer(content[dod_section_start:]):
                if track_dod_match.group(1) == letter:
                    td_start = dod_section_start + track_dod_match.end()
                    # Find next ### heading
                    next_sub = re.search(r"^###\s+", content[td_start:], re.MULTILINE)
                    td_end = td_start + next_sub.start() if next_sub else len(content)
                    dod_text = content[td_start:td_end]
                    dod_items = extract_dod_items(dod_text)
                    break

        tracks.append(SprintTrack(
            name=letter,
            title=title,
            prd_scope=prd_scope,
            files=files,
            validation_criteria=validation_criteria,
            dod_items=dod_items,
        ))

    return tracks


def extract_file_overlap_matrix(content: str) -> list[FileOverlapEntry]:
    """Parse the File Overlap Matrix markdown table.

    Expected format::

        | File | Track A | Track B | Track C | Conflict? |
        |------|---------|---------|---------|-----------|
        | `path/file.py` | WRITE | -- | -- | NONE |

    Args:
        content: Full sprint document markdown.

    Returns:
        List of FileOverlapEntry models.
    """
    # Find the "File Overlap Matrix" section
    matrix_heading = re.search(
        r"^##\s+File\s+Overlap\s+Matrix", content, re.MULTILINE | re.IGNORECASE,
    )
    if not matrix_heading:
        return []

    section_start = matrix_heading.end()
    # Find next ## heading
    next_h2 = re.search(r"^##\s+", content[section_start:], re.MULTILINE)
    section_end = section_start + next_h2.start() if next_h2 else len(content)
    section = content[section_start:section_end]

    # Find table rows
    lines = section.strip().splitlines()
    header_line: str | None = None
    separator_seen = False
    entries: list[FileOverlapEntry] = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue

        cells = [c.strip() for c in stripped.split("|")]
        # Remove empty first/last from leading/trailing |
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if not cells:
            continue

        # Detect header
        if header_line is None and any("file" in c.lower() or "track" in c.lower() for c in cells):
            header_line = stripped
            # Extract track names from header
            track_names: list[str] = []
            for cell in cells[1:]:
                cell_stripped = cell.strip()
                track_match = re.match(r"Track\s+([A-Z])", cell_stripped, re.IGNORECASE)
                if track_match:
                    track_names.append(track_match.group(1))
            continue

        # Skip separator line
        if all(c.replace("-", "").replace("|", "").strip() == "" for c in cells):
            separator_seen = True
            continue

        if not separator_seen:
            continue

        # Data row
        if len(cells) < 2:
            continue

        file_path = cells[0].strip().strip("`").strip()
        if not file_path or file_path.lower() in ("file", ""):
            continue

        # Map track columns using track_names from header
        track_owners: dict[str, str] = {}
        conflict_col: str | None = None

        data_cols = cells[1:]  # everything after file_path
        for j, cell_val in enumerate(data_cols):
            cell_val = cell_val.strip()
            if j < len(track_names):
                track_owners[track_names[j]] = cell_val
            else:
                # Remaining columns after track names (e.g. Conflict?)
                conflict_col = cell_val

        has_conflict = False
        if conflict_col is not None:
            has_conflict = conflict_col.upper() not in ("NONE", "--", "")
        else:
            # Infer conflict from multiple non-empty track owners
            active_owners = [
                v for v in track_owners.values()
                if v.strip() not in ("--", "", "NONE")
            ]
            has_conflict = len(active_owners) > 1

        entries.append(FileOverlapEntry(
            file_path=file_path,
            track_owners=track_owners,
            has_conflict=has_conflict,
        ))

    return entries


def parse_sprint_doc(content: str, source_path: str = "") -> SprintDoc:
    """Parse a sprint planning document into a SprintDoc model.

    Top-level parser that orchestrates extraction of all sprint components.

    Args:
        content: Full sprint document markdown content.
        source_path: Path to the source file (for reference).

    Returns:
        Populated SprintDoc model.

    Raises:
        ValidationError: If the document lacks a sprint number.
    """
    # Extract sprint number
    number_match = _SPRINT_NUMBER_RE.search(content)
    if not number_match:
        raise ValidationError(
            "Sprint document must contain a sprint number (e.g., '# Sprint 11: ...')",
            source_path=source_path,
        )
    sprint_number = int(number_match.group(1))

    # Extract title (text after "# Sprint N: ")
    title_match = re.search(
        r"#\s+Sprint\s+\d+:\s*(.+?)(?:\n|$)", content, re.IGNORECASE,
    )
    title = title_match.group(1).strip() if title_match else ""

    # Extract goal
    goal_match = _GOAL_RE.search(content)
    goal = goal_match.group(1).strip() if goal_match else ""

    # Extract tracks
    tracks = extract_tracks(content)

    # Extract file overlap matrix
    file_overlap_matrix = extract_file_overlap_matrix(content)

    # Extract merge order
    merge_match = _MERGE_ORDER_RE.search(content)
    merge_order = merge_match.group(1).strip() if merge_match else ""

    # Extract top-level DoD items
    dod_items: list[str] = []
    dod_match = _DOD_HEADING_RE.search(content)
    if dod_match:
        dod_start = dod_match.end()
        # Get all checkboxes in DoD section
        next_h2 = re.search(r"^##\s+(?!#)", content[dod_start:], re.MULTILINE)
        dod_end = dod_start + next_h2.start() if next_h2 else len(content)
        dod_section = content[dod_start:dod_end]
        dod_items = extract_dod_items(dod_section)

    return SprintDoc(
        sprint_number=sprint_number,
        title=title,
        goal=goal,
        tracks=tracks,
        file_overlap_matrix=file_overlap_matrix,
        merge_order=merge_order,
        dod_items=dod_items,
        source_path=source_path,
    )


def get_track_by_name(sprint_doc: SprintDoc, name: str) -> SprintTrack:
    """Look up a track by its letter name.

    Args:
        sprint_doc: Parsed sprint document.
        name: Track letter (e.g., "A", "B").

    Returns:
        Matching SprintTrack.

    Raises:
        ValidationError: If the track is not found.
    """
    name_upper = name.upper()
    for track in sprint_doc.tracks:
        if track.name == name_upper:
            return track

    available = [t.name for t in sprint_doc.tracks]
    raise ValidationError(
        f"Track '{name_upper}' not found in sprint {sprint_doc.sprint_number}. "
        f"Available tracks: {available}",
        sprint_number=sprint_doc.sprint_number,
        requested_track=name_upper,
    )
