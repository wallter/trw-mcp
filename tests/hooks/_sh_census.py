"""One shell-script census walker for the hook class gates.

A class gate over the bundled hooks asks "which calls of X sit outside the
helper that owns X?". Each gate supplies the pattern and its allowlist; this
module supplies the part they share: the enclosing function of every matching
line, with comments and heredoc bodies (python programs, messages) skipped.
Allowlists key on (file, function, command text), never on a line number, so an
unrelated edit above a listed call cannot move it off the list.

Used by test_no_shell_json_parser.py (jq, PRD-FIX-154 FR07) and
test_hook_safe_fs_census.py (rm, PRD-FIX-156 FR03).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tests._layout import PACKAGE_ROOT

DATA = PACKAGE_ROOT / "src" / "trw_mcp" / "data"

_FN_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{\s*$")
#: `<<'PY'`, `<<"EOF"`, `<<-EOF`, `<<EOF`: the tag that closes the heredoc body.
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


@dataclass(frozen=True, slots=True)
class ShellSite:
    """One matching line: where it is and which function it belongs to."""

    relpath: str
    lineno: int
    function: str
    source: str


def census(path: Path, pattern: re.Pattern[str], *, owned_blocks: Mapping[str, str] | None = None) -> list[ShellSite]:
    """Every non-comment line of ``path`` that ``pattern`` matches.

    ``owned_blocks`` maps the opening text of a single-quoted multi-line string
    assignment (``_TRW_JSON_GET_PY='``) to the function it belongs to: its body
    is that function's code even though it sits at top level. Heredoc bodies are
    data, not shell, and are skipped.
    """
    owned = owned_blocks or {}
    relpath = path.relative_to(DATA).as_posix() if path.is_relative_to(DATA) else path.name
    sites: list[ShellSite] = []
    function = ""
    block_owner = ""
    heredoc_tag = ""
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if heredoc_tag:
            if stripped == heredoc_tag:
                heredoc_tag = ""
            continue
        opener = next((owner for prefix, owner in owned.items() if stripped.startswith(prefix)), None)
        if opener is not None:
            block_owner = opener
            continue
        if block_owner and stripped == "'":
            block_owner = ""
            continue
        match = _FN_DEF.match(stripped)
        if match:
            function = match.group(1)
        elif stripped == "}":
            function = ""
        if stripped.startswith("#"):
            continue
        if not block_owner:
            tag = _HEREDOC.search(line)
            if tag:
                heredoc_tag = tag.group(1)
        if pattern.search(line):
            sites.append(ShellSite(relpath, lineno, function or block_owner, stripped))
    return sites
