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
        # ``[ \t]+`` rather than ``\s+``: ``\s`` matches a NEWLINE, so the old
        # pattern also matched a top-level ``def`` preceded by blank lines and
        # reported it a second time, at the blank line's number. Invisible while
        # only the first symbol per file was ever emitted (PRD-CORE-267 FR02).
        ("method", re.compile(r"^[ \t]+(?:async\s+)?def\s+(\w+)", re.MULTILINE)),
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


# A single extracted symbol definition:
# (line_num, symbol_type, symbol_name, signature, span_end).
#
# ``span_end`` is the last line that belongs to the definition's own body
# (PRD-CORE-267 FR02). It is deliberately NOT "the line before the next
# definition": that tiling makes every change in a file land inside some
# symbol, which is precisely how a change to module-level code acquired the
# file's first function as an anchor.
_SymbolDef = tuple[int, str, str, str, int]


def _span_end(lines: list[str], def_line: int, next_def_line: int) -> int:
    """Return the last line belonging to the definition starting at *def_line*.

    The body is the run of lines indented more deeply than the definition
    itself, ignoring blank lines, bounded above by *next_def_line*. This works
    for indent-scoped languages and for brace languages alike (a closing brace
    at the definition's own indentation ends the run).
    """
    raw = lines[def_line - 1]
    base_indent = len(raw) - len(raw.lstrip())
    end = def_line
    limit = min(next_def_line - 1, len(lines))
    for index in range(def_line, limit):
        line = lines[index]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        end = index + 1
    return end


def _collect_symbol_defs(content: str, lang: str) -> list[_SymbolDef]:
    """Extract every symbol definition in *content* for language *lang*.

    Returns the definitions sorted ascending by line number, each carrying the
    end of its own body. Each language's patterns are mutually exclusive per
    line (they anchor on distinct leading tokens), so a definition line
    contributes at most one entry.
    """
    found: list[tuple[int, str, str, str]] = []
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

            found.append((line_num, symbol_type, symbol_name, signature))

    found.sort(key=lambda d: d[0])
    # Enforce "at most one definition per line" instead of asserting it: two
    # patterns that overlap would otherwise emit the same symbol twice and the
    # FR02 selection would return duplicate anchors.
    deduped: list[tuple[int, str, str, str]] = []
    for candidate in found:
        if not deduped or deduped[-1][0] != candidate[0]:
            deduped.append(candidate)
    found = deduped
    lines = content.splitlines()
    defs: list[_SymbolDef] = []
    for index, (line_num, symbol_type, symbol_name, signature) in enumerate(found):
        next_line = found[index + 1][0] if index + 1 < len(found) else len(lines) + 1
        defs.append((line_num, symbol_type, symbol_name, signature, _span_end(lines, line_num, next_line)))
    return defs


def _nearest_def(defs: list[_SymbolDef], range_start: int) -> _SymbolDef | None:
    """Return the definition nearest at-or-before *range_start*, or ``None``.

    ``defs`` must be sorted ascending by line. When every definition begins
    after the changed range there is no enclosing symbol, and ``None`` is the
    only truthful answer — the previous behaviour returned the file's FIRST
    definition, which is the fabrication PRD-CORE-267 FR02 removes.
    """
    before = [d for d in defs if d[0] <= range_start]
    return before[-1] if before else None


def _anchor_from_def(file_str: str, symbol: _SymbolDef) -> AnchorDict:
    line_num, symbol_type, symbol_name, signature, _span = symbol
    return {
        "file": file_str,
        "symbol_name": symbol_name,
        "symbol_type": symbol_type,
        "signature": signature,
        "line_range": (line_num, line_num),
    }


def _overlaps(symbol: _SymbolDef, ranges: list[tuple[int, int]]) -> bool:
    """True when any changed range intersects the symbol's own body span."""
    start, end = symbol[0], symbol[4]
    return any(range_start <= end and range_end >= start for range_start, range_end in ranges)


def _anchors_for_file(
    file_str: str,
    defs: list[_SymbolDef],
    ranges: list[tuple[int, int]] | None,
    limit: int,
    *,
    mentioned_names: frozenset[str],
    file_mentioned: bool,
) -> list[AnchorDict]:
    """Select up to *limit* anchors for a single file (PRD-CORE-267 FR02).

    A definition is selected when at least one overlap predicate holds:

    1. its name appears in the learning's own text (``mentioned_names``);
    2. a changed line range intersects its body span;
    3. the learning names the FILE and the definition is the nearest one
       at-or-before a changed range — the relaxation that lets a change to
       module-level code in an explicitly-cited file still anchor.

    A file with definitions but no qualifying predicate contributes NOTHING.
    There is no first-symbol fallback.
    """
    if limit <= 0 or not defs:
        return []

    effective_ranges = ranges or []
    chosen: list[_SymbolDef] = []
    seen_lines: set[int] = set()

    def _take(symbol: _SymbolDef | None) -> None:
        if symbol is None or symbol[0] in seen_lines:
            return
        seen_lines.add(symbol[0])
        chosen.append(symbol)

    for symbol in defs:
        if symbol[2] in mentioned_names or (effective_ranges and _overlaps(symbol, effective_ranges)):
            _take(symbol)

    if file_mentioned:
        for range_start, _range_end in effective_ranges:
            _take(_nearest_def(defs, range_start))

    chosen.sort(key=lambda d: d[0])
    return [_anchor_from_def(file_str, symbol) for symbol in chosen[:limit]]


def generate_anchors(
    modified_files: list[str],
    changed_line_ranges: dict[str, list[tuple[int, int]]],
    *,
    mentioned_names: frozenset[str] = frozenset(),
    mentioned_files: frozenset[str] = frozenset(),
) -> list[AnchorDict]:
    """Extract code symbol anchors from recently modified files (PRD-CORE-111 FR02).

    Every emitted anchor carries demonstrated overlap with the learning
    (PRD-CORE-267 FR02): its name was stated by the author, a changed line
    range intersects its body span, or the learning named the file and the
    definition encloses a change. A file with no qualifying definition yields
    NOTHING — there is no first-symbol fallback. Best-effort otherwise:
    parse/read failures and unsupported files are skipped, never raised.

    Args:
        modified_files: File paths (relative to project root or absolute).
        changed_line_ranges: Mapping of file path -> list of (start, end)
            changed line ranges. Keys should match the entries in
            ``modified_files``.
        mentioned_names: Identifier-shaped tokens the learning's own text
            states; a definition whose name is in this set is anchorable
            without any diff evidence.
        mentioned_files: File-path strings (as supplied in *modified_files*)
            the learning names explicitly.

    Returns:
        List of AnchorDict with keys: file, symbol_name, symbol_type,
        signature, line_range. Empty list if no symbol qualifies.
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
        anchors.extend(
            _anchors_for_file(
                file_str,
                defs,
                ranges,
                _MAX_ANCHORS - len(anchors),
                mentioned_names=mentioned_names,
                file_mentioned=file_path_str in mentioned_files or file_str in mentioned_files,
            )
        )

    return anchors[:_MAX_ANCHORS]


__all__ = ["MARKER_PATTERN", "AnchorDict", "extract_marker_ids", "generate_anchors"]
