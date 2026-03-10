"""Cross-shard DRY enforcement -- n-gram block duplication detection.

PRD-QUAL-039: Detects duplicated code blocks (>= min_block_size lines,
>= 2 occurrences) within or across files, ignoring blank lines and comments.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BlockLocation:
    """Location of a duplicated block within a file."""

    file_path: str
    start_line: int
    end_line: int


@dataclass
class DuplicatedBlock:
    """A code block found duplicated across locations."""

    content: str
    block_hash: str
    locations: list[BlockLocation] = field(default_factory=list)


# Default patterns to ignore when normalizing lines
_COMMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*#"),      # Python comments
    re.compile(r"^\s*//"),     # JS/TS/Go comments
    re.compile(r"^\s*\*"),     # Block comment continuations
    re.compile(r"^\s*/\*"),    # Block comment starts
)

# Default ignore patterns for boilerplate
_DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    r"^\s*(?:from|import)\s+",    # Import lines
    r"^\s*(?:except|finally)\s*:",  # Exception handlers
    r"^\s*(?:pass|\.\.\.)\s*$",   # Pass/ellipsis statements
)


def _is_ignorable_line(line: str) -> bool:
    """Check if a line should be ignored (blank or comment)."""
    stripped = line.strip()
    if not stripped:
        return True
    return any(pat.match(stripped) for pat in _COMMENT_PATTERNS)


def _normalize_line(line: str) -> str:
    """Normalize a line for comparison: strip leading/trailing whitespace."""
    return line.strip()


def _should_ignore_block(
    block_lines: list[str],
    ignore_patterns: list[re.Pattern[str]],
) -> bool:
    """Check if a block is entirely boilerplate that should be ignored."""
    if not block_lines:
        return True
    significant = 0
    for line in block_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(pat.match(stripped) for pat in ignore_patterns):
            continue
        significant += 1
    # Ignore blocks where >80% of lines are boilerplate
    return significant < len(block_lines) * 0.2


def _deduplicate_locations(locations: list[BlockLocation]) -> list[BlockLocation]:
    """Remove overlapping locations within the same file.

    When a block appears in a file, the sliding window produces multiple
    overlapping matches. Keep only the first occurrence per file region.
    """
    result: list[BlockLocation] = []
    # Group by file
    by_file: dict[str, list[BlockLocation]] = {}
    for loc in locations:
        by_file.setdefault(loc.file_path, []).append(loc)

    for _, file_locs in by_file.items():
        file_locs.sort(key=lambda loc: loc.start_line)
        last_end = -1
        for loc in file_locs:
            if loc.start_line > last_end:
                result.append(loc)
                last_end = loc.end_line

    return result


def find_duplicated_blocks(
    file_paths: Sequence[str | Path],
    *,
    min_block_size: int = 5,
    min_occurrences: int = 2,
    ignore_patterns: list[str] | None = None,
) -> list[DuplicatedBlock]:
    """Find duplicated code blocks across a set of files.

    Uses sliding-window n-gram hashing to detect blocks of >= min_block_size
    significant lines (excluding blanks and comments) that appear
    >= min_occurrences times.

    Args:
        file_paths: Files to scan for duplication.
        min_block_size: Minimum number of significant lines for a block.
        min_occurrences: Minimum number of occurrences to report.
        ignore_patterns: Additional regex patterns for lines to ignore.

    Returns:
        List of DuplicatedBlock instances with all locations.
    """
    compiled_ignore = [re.compile(p) for p in (ignore_patterns or [])]
    compiled_ignore.extend(re.compile(p) for p in _DEFAULT_IGNORE_PATTERNS)

    # Map: block_hash -> (content, list of locations)
    block_map: dict[str, tuple[str, list[BlockLocation]]] = {}

    for file_path in file_paths:
        path = Path(file_path)
        if not path.is_file():
            continue
        try:
            raw_lines = path.read_text(
                encoding="utf-8", errors="replace",
            ).splitlines()
        except OSError:
            continue

        # Build list of (original_line_num, normalized_content) for significant lines
        significant: list[tuple[int, str]] = []
        for i, line in enumerate(raw_lines):
            if _is_ignorable_line(line):
                continue
            significant.append((i + 1, _normalize_line(line)))  # 1-indexed

        # Sliding window over significant lines
        for start_idx in range(len(significant) - min_block_size + 1):
            window = significant[start_idx : start_idx + min_block_size]
            block_lines = [content for _, content in window]

            # Skip boilerplate blocks
            if _should_ignore_block(block_lines, compiled_ignore):
                continue

            block_content = "\n".join(block_lines)
            block_hash = hashlib.sha256(block_content.encode()).hexdigest()[:16]

            location = BlockLocation(
                file_path=str(path),
                start_line=window[0][0],
                end_line=window[-1][0],
            )

            if block_hash in block_map:
                block_map[block_hash][1].append(location)
            else:
                block_map[block_hash] = (block_content, [location])

    # Filter to blocks with enough occurrences
    results: list[DuplicatedBlock] = []
    seen_hashes: set[str] = set()
    for block_hash, (content, locations) in block_map.items():
        if len(locations) >= min_occurrences and block_hash not in seen_hashes:
            # Deduplicate overlapping locations in the same file
            deduped = _deduplicate_locations(locations)
            if len(deduped) >= min_occurrences:
                results.append(DuplicatedBlock(
                    content=content,
                    block_hash=block_hash,
                    locations=deduped,
                ))
                seen_hashes.add(block_hash)

    # Sort by number of occurrences descending
    results.sort(key=lambda b: len(b.locations), reverse=True)
    return results


def format_dry_report(
    blocks: list[DuplicatedBlock],
    *,
    max_blocks: int = 10,
) -> str:
    """Format duplication findings as a human-readable report.

    Args:
        blocks: List of duplicated blocks to report.
        max_blocks: Maximum number of blocks to include.

    Returns:
        Markdown-formatted report string.
    """
    if not blocks:
        return "No duplicated blocks found."

    lines: list[str] = [
        f"## DRY Check: {len(blocks)} duplicated block(s) found\n",
    ]

    for i, block in enumerate(blocks[:max_blocks]):
        lines.append(f"### Block {i + 1} ({len(block.locations)} occurrences)")
        lines.append(f"Hash: `{block.block_hash}`\n")
        lines.append("Locations:")
        for loc in block.locations:
            lines.append(
                f"- `{loc.file_path}` lines {loc.start_line}-{loc.end_line}",
            )
        lines.append(f"\n```\n{block.content}\n```\n")

    if len(blocks) > max_blocks:
        lines.append(f"\n... and {len(blocks) - max_blocks} more blocks")

    return "\n".join(lines)
