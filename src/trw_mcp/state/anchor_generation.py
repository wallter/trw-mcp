"""Regex-based code symbol extraction for anchor generation.

Extracts function/class/method/const definitions from source files
using lightweight regex patterns. Best-effort — empty results are valid.
No AST parsing required.

PRD-CORE-111 FR02: Programmatic Anchor Generation
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

import structlog

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
        ("function", re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE)),
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


def generate_anchors(
    modified_files: list[str],
    symbol_context: dict[str, object],
) -> list[AnchorDict]:
    """Extract code symbol anchors from recently modified files.

    Best-effort extraction using regex patterns. Returns up to 3 anchors
    for the most recently modified files that contain recognizable symbols.

    Args:
        modified_files: List of file paths (relative to project root or absolute).
        symbol_context: Optional context hints (unused for now, reserved for future).

    Returns:
        List of AnchorDict with keys: file, symbol_name, symbol_type, signature, line_range.
        Empty list if no symbols found or files are unsupported/binary.
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

        patterns = _PATTERNS.get(lang, [])

        for symbol_type, pattern in patterns:
            if len(anchors) >= _MAX_ANCHORS:
                break

            # Check if we already got an anchor from this file
            if any(a["file"] == str(file_path) for a in anchors):
                break

            for match in pattern.finditer(content):
                if len(anchors) >= _MAX_ANCHORS:
                    break

                symbol_name = match.group(1)
                line_num = content[: match.start()].count("\n") + 1

                # Extract signature (the matched line, truncated)
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                signature = content[line_start:line_end].strip()
                if len(signature) > 200:
                    signature = signature[:200]

                anchors.append(
                    {
                        "file": str(file_path),
                        "symbol_name": symbol_name,
                        "symbol_type": symbol_type,
                        "signature": signature,
                        "line_range": (line_num, line_num),
                    }
                )

                # Only take first symbol per file to get diversity across files
                break

    return anchors[:_MAX_ANCHORS]


__all__ = ["AnchorDict", "MARKER_PATTERN", "extract_marker_ids", "generate_anchors"]
