"""Session-scoped inputs for learning-anchor derivation (PRD-CORE-267 FR01/FR02).

Belongs to the ``_learn_anchors.py`` flow. Extracted so the run resolution,
event reading, diff parsing and learning-text extraction each stay
unit-testable and ``_learn_anchors`` stays under the module-size gate.

Everything here answers one question: *what did THIS session touch, and what
does THIS learning talk about?* Before PRD-CORE-267 the answer was drawn from
whichever peer run had written an event most recently plus a name-only
``git diff`` over the shared working tree, which is how 1,472 of 2,006 anchored
rows in the development store ended up sharing 26 anchor sets.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)

_GIT_TIMEOUT = 5

# +++ b/<path> header in a unified diff identifies the file for following hunks.
_DIFF_FILE_PREFIX = "+++ b/"

# @@ -a,b +c,d @@  — capture the new-side start line (c) and optional count (d).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Identifier-shaped tokens in a learning's prose. A symbol name the author
# actually typed is the strongest available evidence that the learning is about
# that symbol, and it costs nothing to extract.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(frozen=True, slots=True)
class LearningMentions:
    """What a learning's own text names (PRD-CORE-267 FR02 predicates 1 and 2)."""

    #: Identifier-shaped tokens found in summary/detail/evidence.
    names: frozenset[str] = field(default_factory=frozenset)
    #: The concatenated lowercase text, used for path-substring matching.
    text: str = ""

    def mentions_file(self, file_path: Path) -> bool:
        """True when *file_path* is named in the learning text.

        Accepts the full path, any trailing path segment run, or the bare
        basename — authors cite files all three ways, and a citation of
        ``tools/_learn_anchors.py`` names the same file as the absolute form.
        """
        if not self.text:
            return False
        parts = file_path.parts
        if not parts:
            return False
        for start in range(len(parts)):
            candidate = "/".join(parts[start:]).lower()
            if candidate and candidate in self.text:
                return True
        return False


def learning_mentions(summary: str, detail: str, evidence: list[str] | None) -> LearningMentions:
    """Collect the identifiers and raw text a learning states about itself."""
    chunks = [summary or "", detail or "", *(evidence or [])]
    joined = "\n".join(str(chunk) for chunk in chunks)
    if not joined.strip():
        return LearningMentions()
    return LearningMentions(names=frozenset(_IDENTIFIER_RE.findall(joined)), text=joined.lower())


def resolve_session_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return the run directory pinned by THIS caller, or ``None``.

    FR01: the pin is the only admissible source. There is deliberately no
    disk scan and no mtime heuristic — on a shared checkout the mtime-newest
    run belongs to whichever peer session wrote last, not to the caller.
    """
    from trw_mcp.state._paths_pin_mgmt import get_pinned_run

    pinned = get_pinned_run(context=context, session_id=session_id)
    if pinned is None or not pinned.is_dir():
        return None
    return pinned


def _event_path(event: dict[str, object]) -> str:
    """Extract a modified-file path from one event record, or ``""``.

    Three shapes are accepted. The bundled ``post-tool-event.sh`` hook writes
    the FLAT ``file`` key; before FR01 only ``data.path`` and ``path`` were
    read, so no hook-written run ever contributed a single path and the code
    always fell through to the shared-tree ``git diff``.
    """
    data = event.get("data")
    if isinstance(data, dict):
        nested = str(data.get("file") or data.get("path") or "")
        if nested:
            return nested
    return str(event.get("file") or event.get("path") or "")


def modified_files_for_run(run_dir: Path) -> list[str]:
    """Read ``file_modified`` paths from ONE run's ``meta/events.jsonl``.

    Returns the paths as recorded (absolute for hook-written events, relative
    for tool-written ones), de-duplicated, in first-seen order.

    A run with no events file has recorded no edits — that is an answer, not an
    error. A file that exists but cannot be READ is a genuine fault and is left
    to propagate into the caller's single fail-open boundary, so it is never
    silently indistinguishable from "this run touched nothing".
    """
    events_path = run_dir / "meta" / "events.jsonl"
    if not events_path.is_file():
        logger.debug("anchor_run_events_absent", run_dir=str(run_dir))
        return []
    raw = events_path.read_text(encoding="utf-8")

    paths: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:  # trw-fail-silent-allow: a torn tail line in an append-only event log is ordinary; every complete record before it is still read
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("event") or event.get("type") or "") != "file_modified":
            continue
        path = _event_path(event)
        if path and path not in paths:
            paths.append(path)
    return paths


def _child_env(session_id: str | None) -> dict[str, str]:
    """Copy the process environment and preserve the caller's pin identity."""
    env = dict(os.environ)
    if session_id:
        env["TRW_SESSION_ID"] = session_id
    return env


def git_diff_line_ranges(project_root: Path, *, session_id: str | None = None) -> dict[str, list[tuple[int, int]]]:
    """Parse ``git diff -U0 HEAD`` hunk headers into per-file changed line ranges.

    Ranges use new-side (post-change) line numbers. A pure-deletion hunk
    (``+c,0``) anchors at line ``c``. The result is only ever consulted for
    files the caller's own run already named, so a peer's hunks in an
    untouched file cannot reach anchor selection.
    """
    result = subprocess.run(
        ["git", "diff", "-U0", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        cwd=str(project_root),
        env=_child_env(session_id),
    )
    if result.returncode != 0:
        return {}

    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith(_DIFF_FILE_PREFIX):
            current = line[len(_DIFF_FILE_PREFIX) :].strip()
            continue
        if line.startswith("+++ "):  # e.g. "+++ /dev/null" — no trackable file
            current = None
            continue
        if current and line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if match is None:
                continue
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) else 1
            end = start if count == 0 else start + count - 1
            ranges.setdefault(current, []).append((start, end))
    return ranges


__all__ = [
    "LearningMentions",
    "git_diff_line_ranges",
    "learning_mentions",
    "modified_files_for_run",
    "resolve_session_run",
]
