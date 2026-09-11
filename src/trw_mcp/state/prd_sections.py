"""Internal, hash-guarded replacement of one PRD Execution plan section.

This is not PRD validation or admission. The scanner supports ATX headings,
YAML frontmatter and backtick/tilde fences; ambiguous unclosed blocks fail closed.
Outside-section UTF-8 bytes are preserved, including line endings and EOF state.
There are no configuration knobs or public tool registrations.
"""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateWriter, lock_for_rmw

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def _headings(text: str) -> list[tuple[int, int, str]]:
    """Return (character offset, level, title) outside frontmatter/fences."""
    headings: list[tuple[int, int, str]] = []
    fence = ""
    frontmatter = False
    offset = 0
    previous = ""
    previous_offset = 0
    for index, raw in enumerate(text.splitlines(keepends=True)):
        line = raw.rstrip("\r\n")
        next_previous = ""
        if index == 0 and line.lstrip("\ufeff") == "---":
            frontmatter = True
        elif frontmatter:
            if line in {"---", "..."}:
                frontmatter = False
        elif fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                fence = ""
        elif match := _FENCE.match(line):
            marker, info = match.groups()
            if marker[0] == "~" or "`" not in info:
                fence = marker
        elif match := _HEADING.match(line):
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
            headings.append((offset, len(match[1]), title))
        elif _SETEXT.match(line):
            # Setext boundaries must not let replacement text escape its section.
            if previous.strip():
                headings.append((previous_offset, 1 if line.lstrip().startswith("=") else 2, previous.strip()))
        else:
            next_previous = line
        # Only ordinary prose can precede a Setext underline, never a fence,
        # frontmatter delimiter, ATX heading or another underline.
        previous, previous_offset = next_previous, offset
        offset += len(raw)
    if frontmatter or fence:
        raise ValueError("Unclosed frontmatter or code fence")
    return headings


def _section(text: str) -> tuple[int, int]:
    headings = _headings(text)
    matches = [
        (offset, level) for offset, level, title in headings if level == 2 and title.casefold() == "execution plan"
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Execution plan section")
    start = matches[0][0]
    if not _HEADING.match(text[start:].splitlines()[0]):
        raise ValueError("Execution plan must use an ATX heading")
    end = next((offset for offset, level, _ in headings if offset > start and level <= 2), len(text))
    return start, end


def _read_target(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Target must be an existing regular nonsymlink file")
    return path.read_bytes()


def update_execution_plan(path: Path, replacement: str, *, expected_sha256: str) -> str:
    """Replace the full single section, returning the resulting whole-file SHA-256.

    ``replacement`` starts with an ATX ``## Execution plan`` heading and contains
    no peer/parent section. If a following section exists, end replacement with a
    newline. Invalid input, stale hashes and I/O failures raise ``StateError``.

    Cooperating callers serialize via a persistent sibling ``<name>.lock`` file
    (created/truncated even on rejected updates). An immediate pre-write reread
    catches intervening changes, but is NOT CAS against noncooperating editors.
    Atomic replacement inherits FileStateWriter's metadata/durability semantics;
    only outside-section content bytes, not inode metadata, are preserved.
    """
    try:
        # Reject missing/nonregular targets before lock creation can make parents.
        _read_target(path)
        path = path.parent.resolve() / path.name
        with lock_for_rmw(path):
            original = _read_target(path)
            if hashlib.sha256(original).hexdigest() != expected_sha256:
                raise ValueError("Stale PRD SHA-256; reread before retrying")
            text = original.decode("utf-8")
            start, end = _section(text)
            replacement_start, replacement_end = _section(replacement)
            if (
                replacement_start != 0
                or replacement_end != len(replacement)
                or not _HEADING.match(replacement.splitlines()[0])
            ):
                raise ValueError("Replacement must contain only the full Execution plan section")
            if end < len(text) and not replacement.endswith(("\n", "\r")):
                raise ValueError("Replacement needs a final newline before the next section")
            updated = text[:start] + replacement + text[end:]
            encoded = updated.encode("utf-8")
            if _read_target(path) != original:
                raise ValueError("PRD changed immediately before write; reread before retrying")
            FileStateWriter().write_text(path, updated)
            return hashlib.sha256(encoded).hexdigest()
    except StateError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise StateError(f"Cannot update Execution plan: {exc}", path=str(path)) from exc
