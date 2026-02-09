"""Shared PRD utility functions — parsing, extraction, and frontmatter updates.

Extracted from tools/requirements.py (PRD-FIX-006) to provide shared
infrastructure for PRD-CORE-007, CORE-008, CORE-009, and CORE-010.

All functions are pure or file-scoped — no MCP tool registration side effects.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from ruamel.yaml import YAML

from trw_mcp.exceptions import StateError

logger = structlog.get_logger()

# Compiled regex patterns (module-level for performance)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_SECTION_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)
_PRD_REF_RE = re.compile(r"PRD-[A-Z]+-\d{3}")

_AMBIGUOUS_TERMS: list[str] = [
    "fast", "quick", "efficient", "user-friendly", "robust",
    "scalable", "flexible", "easy", "simple", "intuitive",
    "adequate", "sufficient", "as appropriate", "etc.",
    "and so on", "various", "multiple", "many",
]

# Pre-compile ambiguity patterns for performance
# Terms ending in '.' need special handling: \b doesn't match after '.'
_AMBIGUITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = []
for _term in _AMBIGUOUS_TERMS:
    if _term.endswith("."):
        # Match the term followed by a space, end-of-string, or another punctuation
        _pat = re.compile(rf"\b{re.escape(_term)}(?=\s|$|[,;:!?\)])", re.IGNORECASE)
    else:
        _pat = re.compile(rf"\b{re.escape(_term)}\b", re.IGNORECASE)
    _AMBIGUITY_PATTERNS.append((_term, _pat))

# Patterns for non-substantive lines in content density calculation
_NON_SUBSTANTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*$"),                          # blank lines
    re.compile(r"^\s*---\s*$"),                    # horizontal rules
    re.compile(r"^\s*<!--.*?-->\s*$"),             # HTML comments (single-line)
    re.compile(r"^\s*\|[\s\-:|]+\|\s*$"),          # table separator rows
    re.compile(r"^\s*#"),                           # heading lines
]


def parse_frontmatter(content: str) -> dict[str, object]:
    """Parse YAML frontmatter from markdown content.

    Extracts the YAML block between ``---`` delimiters at the start
    of the document. If a nested ``prd`` key is found, its contents
    are flattened to the top level (AARE-F convention).

    Args:
        content: Markdown content with optional YAML frontmatter.

    Returns:
        Parsed frontmatter as a dict, or empty dict if none found.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    yaml = YAML()
    try:
        data = yaml.load(match.group(1))
        if isinstance(data, dict):
            # Flatten nested 'prd' key if present (AARE-F template nests under 'prd')
            if "prd" in data and isinstance(data["prd"], dict):
                prd_data: dict[str, object] = dict(data["prd"])
                for key, val in data.items():
                    if key != "prd":
                        prd_data[key] = val
                return prd_data
            return dict(data)
    except Exception:
        pass
    return {}


def extract_sections(content: str) -> list[str]:
    """Extract ``## N. Section Name`` headings from PRD markdown.

    Only matches numbered section headings (e.g. ``## 1. Problem Statement``),
    not unnumbered headings like ``## Appendix`` or ``### Subsection``.

    Args:
        content: Markdown content.

    Returns:
        List of section heading names found (without the ``## N.`` prefix).
    """
    return _SECTION_HEADING_RE.findall(content)


def detect_ambiguity(content: str) -> list[str]:
    """Detect ambiguous terms in PRD content using word-boundary matching.

    Scans for 18 known ambiguous terms (e.g. "fast", "scalable", "etc.")
    using case-insensitive whole-word matching to avoid false positives
    like "breakfast" matching "fast".

    Args:
        content: PRD markdown content.

    Returns:
        List of ambiguous terms found in the content.
    """
    found: list[str] = []
    for term, pattern in _AMBIGUITY_PATTERNS:
        if pattern.search(content):
            found.append(term)
    return found


def compute_content_density(content: str) -> float:
    """Calculate the ratio of substantive content lines to total lines.

    Non-substantive lines include: blank lines, horizontal rules (``---``),
    HTML comment placeholders (``<!-- ... -->``), table separator rows,
    and heading lines. Everything else is considered substantive.

    Args:
        content: Markdown content string.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 for empty content.
    """
    lines = content.split("\n")
    if not lines:
        return 0.0

    total = len(lines)
    if total == 0:
        return 0.0

    substantive = 0
    for line in lines:
        is_non_substantive = any(p.match(line) for p in _NON_SUBSTANTIVE_PATTERNS)
        if not is_non_substantive:
            substantive += 1

    return substantive / total


def extract_prd_refs(content: str) -> list[str]:
    """Extract PRD references from markdown content.

    Scans for the pattern ``PRD-{CATEGORY}-{NNN}`` (e.g. ``PRD-CORE-007``,
    ``PRD-FIX-006``). Returns a deduplicated, sorted list of matched IDs.

    Args:
        content: Markdown content to scan.

    Returns:
        Sorted list of unique PRD reference IDs found.
    """
    matches = _PRD_REF_RE.findall(content)
    return sorted(set(matches))


def update_frontmatter(path: Path, updates: dict[str, object]) -> None:
    """Update YAML frontmatter fields in a PRD file using round-trip YAML.

    Preserves existing formatting, comments, and key ordering for all
    non-updated fields. Updates are merged into the ``prd`` sub-key
    if present, otherwise into the top-level mapping.

    Args:
        path: Path to the PRD markdown file.
        updates: Dictionary of fields to update (e.g. ``{"status": "approved"}``).

    Raises:
        StateError: If the file does not exist or has no frontmatter.
    """
    if not path.exists():
        raise StateError(f"PRD file not found: {path}", path=str(path))

    content = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise StateError(f"No YAML frontmatter found in: {path}", path=str(path))

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        fm_text = match.group(1)
        data = yaml.load(fm_text)
        if not isinstance(data, dict):
            raise StateError(
                f"Frontmatter is not a mapping in: {path}", path=str(path)
            )

        # Determine target dict: nested 'prd' key or top-level
        target = data.get("prd", data) if "prd" in data and isinstance(data["prd"], dict) else data

        # Apply updates (support nested dicts via recursive merge)
        _deep_merge(target, updates)

        # Serialize updated frontmatter
        from io import StringIO
        stream = StringIO()
        yaml.dump(data, stream)
        new_fm = stream.getvalue()

        # Reconstruct file: new frontmatter + original body
        body = content[match.end():]
        new_content = f"---\n{new_fm}---{body}"

        # Atomic write: write to temp, then rename
        import tempfile
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=str(path.parent), suffix=".md.tmp"
        )
        tmp_path = Path(tmp_path_str)
        try:
            tmp_path.write_text(new_content, encoding="utf-8")
            tmp_path.rename(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        finally:
            import os
            try:
                os.close(tmp_fd)
            except OSError:
                pass

        logger.info("frontmatter_updated", path=str(path), fields=list(updates.keys()))

    except StateError:
        raise
    except Exception as exc:
        raise StateError(
            f"Failed to update frontmatter: {exc}", path=str(path)
        ) from exc


def _deep_merge(target: object, source: dict[str, object]) -> None:
    """Recursively merge source dict into target dict (in-place).

    Args:
        target: Target mapping to merge into.
        source: Source mapping with values to apply.
    """
    if not isinstance(target, dict):
        return
    for key, value in source.items():
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(target[key], value)
        else:
            target[key] = value
