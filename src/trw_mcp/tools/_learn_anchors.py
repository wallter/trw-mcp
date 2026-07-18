"""Anchor resolution for the ``trw_learn()`` flow (PRD-CORE-111 FR04).

Belongs to the ``_learn_impl.py`` flow; extracted so ``_learn_impl`` stays under
the module-size gate and the modified-file/line-range discovery is unit-testable
in isolation.

Determines recently-modified files (run ``events.jsonl`` first, ``git diff``
fallback) and their changed line ranges (``git diff -U0``), generates code
anchors, and computes the initial anchor validity. Fail-open throughout: any
failure yields ``([], 1.0)`` so a learning is still created without anchors.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_GIT_TIMEOUT = 5

# +++ b/<path> header in a unified diff identifies the file for following hunks.
_DIFF_FILE_PREFIX = "+++ b/"

# @@ -a,b +c,d @@  — capture the new-side start line (c) and optional count (d).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _modified_files_from_events(trw_dir: Path) -> list[str]:
    """Read ``file_modified`` event paths from the most recent run's events.jsonl.

    Run directories nest ``events.jsonl`` under ``meta/`` and may use several
    layouts, so we glob rather than assume the PROPER layout. The newest file by
    mtime is treated as the active run. Returns relative paths (as recorded).
    """
    try:
        candidates = list(trw_dir.glob("**/meta/events.jsonl"))
    except OSError:
        return []
    if not candidates:
        return []

    try:
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
    except OSError:
        return []

    paths: list[str] = []
    try:
        raw = latest.read_text(encoding="utf-8")
    except OSError:
        return []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        # Support both {"event": "file_modified", "data": {"path": ...}} and the
        # flatter {"type": "file_modified", "path": ...} shape.
        kind = str(event.get("event") or event.get("type") or "")
        if kind != "file_modified":
            continue
        path = ""
        data = event.get("data")
        if isinstance(data, dict):
            path = str(data.get("path") or "")
        if not path:
            path = str(event.get("path") or "")
        if path and path not in paths:
            paths.append(path)
    return paths


def _child_env(session_id: str | None) -> dict[str, str]:
    """Copy the process environment and preserve the caller's pin identity."""
    env = dict(os.environ)
    if session_id:
        env["TRW_SESSION_ID"] = session_id
    return env


def _git_diff_name_only(project_root: Path, *, session_id: str | None = None) -> list[str]:
    """Return files changed vs HEAD via ``git diff --name-only HEAD``."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        cwd=str(project_root),
        env=_child_env(session_id),
    )
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def _git_diff_line_ranges(project_root: Path, *, session_id: str | None = None) -> dict[str, list[tuple[int, int]]]:
    """Parse ``git diff -U0 HEAD`` hunk headers into per-file changed line ranges.

    Ranges use new-side (post-change) line numbers. A pure-deletion hunk
    (``+c,0``) anchors at line ``c``.
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


def resolve_learn_anchors(
    project_root: Path,
    trw_dir: Path,
    learning_id: str,
    *,
    session_id: str | None = None,
) -> tuple[list[dict[str, object]], float]:
    """Resolve code anchors + initial validity for a new learning (FR04).

    Returns ``(anchors, anchor_validity)``. Always fail-open: on any error the
    learning is created with no anchors and validity 1.0.
    """
    anchors: list[dict[str, object]] = []
    try:
        # FR04 step 1: run events first, git diff fallback.
        modified_rel = _modified_files_from_events(trw_dir)
        if not modified_rel:
            modified_rel = _git_diff_name_only(project_root, session_id=session_id)
        if not modified_rel:
            return [], 1.0

        # FR04 step 2: changed line ranges from git diff -U0.
        line_ranges_rel = _git_diff_line_ranges(project_root, session_id=session_id)

        # Key both the file list and range map by absolute path so
        # generate_anchors can read the files and match ranges.
        modified_abs = [str(project_root / f) for f in modified_rel]
        ranges_abs: dict[str, list[tuple[int, int]]] = {
            str(project_root / rel): rng for rel, rng in line_ranges_rel.items()
        }

        from trw_mcp.state.anchor_generation import generate_anchors

        raw_anchors = generate_anchors(modified_abs, ranges_abs)
        if raw_anchors:
            anchors = [dict(a) for a in raw_anchors]
    except Exception:  # justified: fail-open, anchor generation is best-effort
        logger.debug("anchor_generation_skipped", exc_info=True)
        return [], 1.0

    if not anchors:
        return [], 1.0

    anchor_validity = 1.0
    try:
        from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

        anchor_validity = compute_anchor_validity(anchors, str(project_root), learning_id=learning_id)
    except Exception:  # justified: fail-open, validity computation is best-effort
        logger.debug("anchor_validity_computation_skipped", exc_info=True)

    return anchors, anchor_validity


__all__ = ["resolve_learn_anchors"]
