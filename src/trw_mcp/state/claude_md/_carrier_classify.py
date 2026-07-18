"""Instruction-file classification: pointer / content / empty detection.

Belongs to the ``_instruction_carrier.py`` facade (PRD-CORE-203). These pure
classification helpers were split out so the carrier module stays under the
``state/claude_md`` 350-line gate. The public symbols
(``InstructionFileClass``, ``InstructionFileClassification``,
``classify_instruction_file``) are re-exported through ``_instruction_carrier``
and then ``state/claude_md/__init__.py`` — importers keep their existing paths.

Pure module: reads only the classified path, performs no writes and no network
access. Marker matching is line-anchored whole-line (stripped equality), never a
substring scan (the 705-line ROADMAP corruption lesson — see ``.claude/rules/
trw-mcp-python.md`` §Marker / Sentinel Matching).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
)

# An in-file import directive is a WHOLE line equal to ``@<non-whitespace>``.
# Line-anchored whole-line matching per the trw-mcp marker rule (.claude/rules/
# trw-mcp-python.md): an inline ``@mention`` inside prose is NOT a directive.
_IMPORT_DIRECTIVE_RE = re.compile(r"^@(\S+)$")


class InstructionFileClass(str, Enum):
    """Coarse classification of an instruction file's substantive content."""

    EMPTY = "empty"
    POINTER = "pointer"
    CONTENT = "content"


@dataclass(frozen=True)
class InstructionFileClassification:
    """Result of :func:`classify_instruction_file`."""

    kind: InstructionFileClass
    import_targets: tuple[str, ...] = ()


def _strip_marker_region(lines: list[str]) -> list[str]:
    """Return *lines* with the TRW marker region removed (line-anchored).

    Drops the ``<!-- trw:start -->``..``<!-- trw:end -->`` span and any
    immediately-preceding ``TRW_AUTO_COMMENT`` / blank lines. Matching is
    whole-line (stripped equality), so a marker mentioned inside prose or a code
    block is ignored — never a substring match (the 705-line ROADMAP corruption
    lesson).
    """
    out: list[str] = []
    in_region = False
    for line in lines:
        stripped = line.strip()
        if not in_region and stripped == TRW_MARKER_START:
            in_region = True
            # Drop the auto-comment + blank lines that precede the start marker.
            while out and out[-1].strip() in (TRW_AUTO_COMMENT, ""):
                out.pop()
            continue
        if in_region:
            if stripped == TRW_MARKER_END:
                in_region = False
            continue
        out.append(line)
    return out


def _is_skippable(stripped: str) -> bool:
    """Non-substantive line: blank, a full-line HTML comment, or a bare heading."""
    if not stripped:
        return True
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return True
    return bool(re.match(r"^#{1,6}\s+\S", stripped))


def classify_instruction_file(path: Path) -> InstructionFileClassification:
    """Classify an instruction file as EMPTY, POINTER, or CONTENT (FR03, NFR04).

    Pure: reads only *path*, performs no writes and no network access. After
    removing the TRW marker region, blank lines, full-line HTML comments, and
    bare markdown headings, the file is a POINTER iff every remaining
    substantive line is an import directive (``^@\\S+``). A file with no
    substantive lines is EMPTY; anything else is CONTENT.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return InstructionFileClassification(InstructionFileClass.EMPTY)

    substantive = _strip_marker_region(text.splitlines())
    targets: list[str] = []
    saw_substantive = False
    for line in substantive:
        stripped = line.strip()
        if _is_skippable(stripped):
            continue
        saw_substantive = True
        match = _IMPORT_DIRECTIVE_RE.match(stripped)
        if match is None:
            return InstructionFileClassification(InstructionFileClass.CONTENT)
        targets.append(match.group(1))

    if not saw_substantive:
        return InstructionFileClassification(InstructionFileClass.EMPTY)
    return InstructionFileClassification(InstructionFileClass.POINTER, tuple(targets))
