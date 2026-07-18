"""Regex-based code symbol extraction for anchor generation.

Extracts function/class/method/const definitions from source files
using lightweight regex patterns. Best-effort — empty results are valid.
No AST parsing required.

PRD-CORE-111 FR02: Programmatic Anchor Generation
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from typing_extensions import TypedDict

logger = structlog.get_logger(__name__)


class AnchorDict(TypedDict):
    """Typed dictionary for code symbol anchors returned by generate_anchors."""

    file: str
    symbol_name: str
    symbol_type: str
    signature: str
    line_range: tuple[int, int]


# Max anchors per learning
_MAX_ANCHORS = 3

# Language detection by file extension
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}

# Symbol extraction patterns per language
# Each tuple is (symbol_type, compiled_pattern) where group(1) is the symbol name
_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"^(?:async\s+)?def\s+(\w+)", re.MULTILINE)),
        ("method", re.compile(r"^\s+(?:async\s+)?def\s+(\w+)", re.MULTILINE)),
    ],
    "javascript": [
        ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("const", re.compile(r"^(?:export\s+)?const\s+(\w+)\s*[=:]", re.MULTILINE)),
        ("class", re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)", re.MULTILINE)),
    ],
    "typescript": [
        ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("const", re.compile(r"^(?:export\s+)?const\s+(\w+)\s*[=:]", re.MULTILINE)),
        ("type", re.compile(r"^(?:export\s+)?(?:type|interface)\s+(\w+)", re.MULTILINE)),
        ("class", re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)", re.MULTILINE)),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE)),
        ("method", re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(", re.MULTILINE)),
    ],
    "rust": [
        ("function", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE)),
        # Rust struct maps to the "class" symbol_type (impl target / class-equivalent).
        ("class", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)", re.MULTILINE)),
        ("impl", re.compile(r"^impl(?:<[^>]*>)?\s+(?:\w+\s+for\s+)?(\w+)", re.MULTILINE)),
    ],
}

# ---------------------------------------------------------------------------
# Inline comment marker regex (PRD-CORE-111 FR05)
# Matches: mcp.trw.recall(id=L-xxxx) or mcp.trw.recall(id=L-xxxx,L-yyyy)
# Supports both old 8-char hex IDs and new 4-char base62 IDs (4-8 chars).
# ---------------------------------------------------------------------------
MARKER_PATTERN = re.compile(r"mcp\.trw\.recall\(id=([A-Za-z]-[a-zA-Z0-9]{4,8}(?:,[A-Za-z]-[a-zA-Z0-9]{4,8})*)\)")


def extract_marker_ids(text: str) -> list[str]:
    """Extract learning IDs from inline comment markers.

    Supports single and multiple IDs:
    - mcp.trw.recall(id=L-a3Fq)
    - mcp.trw.recall(id=L-a3Fq,L-b2Xp)

    Args:
        text: Source code text to search.

    Returns:
        List of unique learning IDs found.
    """
    ids: list[str] = []
    for match in MARKER_PATTERN.finditer(text):
        for id_str in match.group(1).split(","):
            id_str = id_str.strip()
            if id_str and id_str not in ids:
                ids.append(id_str)
    return ids


# A single extracted symbol definition: (line_num, symbol_type, symbol_name, signature)
_SymbolDef = tuple[int, str, str, str]


def _collect_symbol_defs(content: str, lang: str) -> list[_SymbolDef]:
    """Extract every symbol definition in *content* for language *lang*.

    Returns the definitions sorted ascending by line number. Each language's
    patterns are mutually exclusive per line (they anchor on distinct
    leading tokens), so a definition line contributes at most one entry.
    """
    defs: list[_SymbolDef] = []
    for symbol_type, pattern in _PATTERNS.get(lang, []):
        for match in pattern.finditer(content):
            symbol_name = match.group(1)
            line_num = content[: match.start()].count("\n") + 1

            # Extract the matched line as the signature (truncated to 200 chars).
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            if line_end == -1:
                line_end = len(content)
            signature = content[line_start:line_end].strip()[:200]

            defs.append((line_num, symbol_type, symbol_name, signature))

    defs.sort(key=lambda d: d[0])
    return defs


def _nearest_def(defs: list[_SymbolDef], range_start: int) -> _SymbolDef:
    """Return the definition whose start line is nearest at-or-before *range_start*.

    ``defs`` must be sorted ascending by line. When every definition begins
    after the changed range (e.g. a change above the first symbol), the first
    definition in the file is returned so a plausible anchor is still emitted.
    """
    before = [d for d in defs if d[0] <= range_start]
    if before:
        return before[-1]  # largest line <= range_start == nearest at-or-before
    return defs[0]


def _anchor_from_def(file_str: str, symbol: _SymbolDef) -> AnchorDict:
    line_num, symbol_type, symbol_name, signature = symbol
    return {
        "file": file_str,
        "symbol_name": symbol_name,
        "symbol_type": symbol_type,
        "signature": signature,
        "line_range": (line_num, line_num),
    }


def _anchors_for_file(
    file_str: str,
    defs: list[_SymbolDef],
    ranges: list[tuple[int, int]] | None,
    limit: int,
) -> list[AnchorDict]:
    """Select up to *limit* anchors for a single file.

    With changed line *ranges*, one anchor is emitted per range (deduplicated)
    anchored to the symbol nearest at-or-before that range's start. Without
    ranges, the first symbol in the file is emitted (legacy fallback so callers
    that supply no ranges keep working).
    """
    if limit <= 0 or not defs:
        return []

    if not ranges:
        return [_anchor_from_def(file_str, defs[0])]

    chosen: list[_SymbolDef] = []
    seen_lines: set[int] = set()
    for range_start, _range_end in ranges:
        symbol = _nearest_def(defs, range_start)
        if symbol[0] not in seen_lines:
            seen_lines.add(symbol[0])
            chosen.append(symbol)

    if not chosen:  # ranges were empty tuples / produced nothing usable
        chosen = [defs[0]]

    return [_anchor_from_def(file_str, s) for s in chosen[:limit]]


def generate_anchors(
    modified_files: list[str],
    changed_line_ranges: dict[str, list[tuple[int, int]]],
) -> list[AnchorDict]:
    """Extract code symbol anchors from recently modified files (PRD-CORE-111 FR02).

    For each modified file the nearest symbol definition at-or-before each
    changed line range is selected. A file with N changed ranges yields up to
    N anchors (bounded globally by ``_MAX_ANCHORS``). When no ranges are
    supplied for a file, the first symbol in that file is used (legacy
    fallback). Best-effort — parse/read failures and unsupported files are
    skipped, never raised.

    Args:
        modified_files: File paths (relative to project root or absolute).
        changed_line_ranges: Mapping of file path -> list of (start, end)
            changed line ranges. Keys should match the entries in
            ``modified_files``. Missing/empty entries trigger the fallback.

    Returns:
        List of AnchorDict with keys: file, symbol_name, symbol_type,
        signature, line_range. Empty list if no symbols are found.
    """
    if not modified_files:
        return []

    anchors: list[AnchorDict] = []

    for file_path_str in modified_files:
        if len(anchors) >= _MAX_ANCHORS:
            break

        file_path = Path(file_path_str)

        # Determine language by file extension
        lang = _LANG_MAP.get(file_path.suffix.lower())
        if not lang:
            continue

        # Read file content (skip binary/unreadable)
        try:
            if not file_path.is_file():
                continue
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("anchor_file_read_failed", file=str(file_path), error=type(exc).__name__)
            continue

        # Check for binary content (null bytes)
        if "\x00" in content:
            continue

        defs = _collect_symbol_defs(content, lang)
        if not defs:
            continue

        # Ranges may be keyed by the raw path string or its normalized form.
        ranges = changed_line_ranges.get(file_path_str)
        if ranges is None:
            ranges = changed_line_ranges.get(str(file_path))

        file_str = str(file_path)
        anchors.extend(_anchors_for_file(file_str, defs, ranges, _MAX_ANCHORS - len(anchors)))

    return anchors[:_MAX_ANCHORS]


__all__ = ["MARKER_PATTERN", "AnchorDict", "extract_marker_ids", "generate_anchors"]
