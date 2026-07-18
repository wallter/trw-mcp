"""Event readers and complexity-drift checks for delivery gates."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import (
    COMPLEXITY_DRIFT_MULTIPLIER,
    REVIEW_SCOPE_FILE_THRESHOLD,
)

logger = structlog.get_logger(__name__)


def _read_run_events(run_path: Path, reader: FileStateReader) -> list[dict[str, object]]:
    """Read events.jsonl for a run, returning empty list on any error.

    Centralised helper — called once by ``check_delivery_gates`` and passed
    to individual gate functions so events.jsonl is read at most once.
    """
    events_path = run_path / "meta" / "events.jsonl"
    try:
        if reader.exists(events_path):
            return reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open, event read must not block delivery
        logger.warning("run_events_read_failed", run_path=str(run_path), exc_info=True)
    return []


def _normalize_event_path(raw: str, repo_root: Path | None = None) -> str:
    """Normalize a ``file_modified`` event path to a repo-relative dedup key.

    Collapses redundant separators / ``.`` / ``..`` via ``os.path.normpath``.
    When an absolute path falls under ``repo_root`` it is reduced to its
    repo-relative form, so the same file recorded once as an absolute path and
    once as a repo-relative path dedupes to a single count.
    """
    norm = os.path.normpath(raw)
    if repo_root is not None and os.path.isabs(norm):
        try:
            rel = os.path.relpath(norm, str(repo_root))
        except ValueError:  # different drive (Windows) — leave absolute
            return norm
        if not rel.startswith(".."):
            return rel
    return norm


def _count_file_modified(
    events: list[dict[str, object]],
    repo_root: Path | None = None,
) -> int:
    """Count DISTINCT normalized file paths across ``file_modified`` events.

    PRD-QUAL-101-FR02a: the review-scope gate measures *delivery size*, so N
    edits to ONE file must count once. The pre-FR02a implementation summed
    event occurrences, inflating the count (N edits → N). Paths are read from
    the top-level ``file`` field that ``post-tool-event.sh`` writes (see
    ``append_event`` in ``lib-trw.sh``; ``FileEventLogger.log_event`` likewise
    flattens a ``file`` key to the top level), normalized to a repo-relative
    key, then deduped.

    Events with NO ``file`` field (non-hook-sourced events, hook gaps, or
    Bash-driven changes that emit no path) are NOT dropped: each is counted as
    its own unit. Absence of a path can therefore only keep the count class the
    same or make it larger — never silently shrink it, which would weaken the
    gate.
    """
    distinct: set[str] = set()
    pathless = 0
    for ev in events:
        if str(ev.get("event", "")) != "file_modified":
            continue
        raw = str(ev.get("file", "")).strip()
        if not raw:
            pathless += 1
            continue
        distinct.add(_normalize_event_path(raw, repo_root))
    return len(distinct) + pathless


def _events_since_last_session_start(
    events: list[dict[str, object]],
    session_id: str | None = None,
) -> list[dict[str, object]]:
    """Return the caller's events after its current logical-session boundary.

    Scoped callers use the first matching ``session_start`` after their last
    matching ``trw_deliver_complete``. Repeated starts with the same stable ID
    are compaction/resume boundaries and must not hide pre-compaction edits.
    Foreign scoped events are filtered; unscoped legacy events remain visible
    conservatively to avoid review-scope and complexity-drift false negatives.
    Unscoped callers retain the legacy last-start behavior.
    """
    last_session_idx = -1
    if session_id is None:
        for i, ev in enumerate(events):
            if str(ev.get("event", "")) == "session_start":
                last_session_idx = i
    else:
        last_delivery_idx = max(
            (
                i
                for i, ev in enumerate(events)
                if str(ev.get("event", "")) == "trw_deliver_complete" and str(ev.get("session_id", "")) == session_id
            ),
            default=-1,
        )
        last_session_idx = next(
            (
                i
                for i, ev in enumerate(events[last_delivery_idx + 1 :], start=last_delivery_idx + 1)
                if str(ev.get("event", "")) == "session_start" and str(ev.get("session_id", "")) == session_id
            ),
            -1,
        )
        has_target_event = any(str(ev.get("session_id", "")) == session_id for ev in events)
        if last_session_idx < 0 and not has_target_event:
            for i, ev in enumerate(events):
                if str(ev.get("event", "")) == "session_start" and not ev.get("session_id"):
                    last_session_idx = i
    window = events if last_session_idx < 0 else events[last_session_idx + 1 :]
    if session_id is None:
        return window
    return [ev for ev in window if not ev.get("session_id") or str(ev.get("session_id")) == session_id]


def _count_file_modified_current_session(
    events: list[dict[str, object]],
    repo_root: Path | None = None,
    session_id: str | None = None,
) -> int:
    """Count DISTINCT modified file paths in the current session only.

    Uses ``session_start`` as the session boundary marker. Events from
    previous sessions (before the last ``session_start``) are excluded. Within
    the window, distinct-path semantics (FR02a) apply — see
    ``_count_file_modified``.
    """
    session_events = _events_since_last_session_start(events, session_id)
    return _count_file_modified(session_events, repo_root)


def _project_root_from_run(run_path: Path) -> Path | None:
    """Walk up from a run dir to the project root (the parent of ``.trw/``).

    Returns None when no ``.trw`` marker is found, in which case path
    normalization falls back to ``os.path.normpath`` only (still correct for
    dedup — the hook records one consistent path form per file per session).
    """
    for parent in run_path.parents:
        if (parent / ".trw").is_dir():
            return parent
    return None


def _read_run_yaml(run_path: Path, reader: FileStateReader) -> dict[str, object]:
    """Read run.yaml, returning empty dict on any error."""
    run_yaml_path = run_path / "meta" / "run.yaml"
    try:
        if run_yaml_path.exists():
            return reader.read_yaml(run_yaml_path)
    except Exception:  # justified: fail-open, run.yaml read must not block delivery
        logger.warning("run_yaml_read_failed", run_path=str(run_path), exc_info=True)
    return {}


def _read_complexity_class(run_path: Path, reader: FileStateReader) -> str:
    """Read the complexity_class from run.yaml, or return empty string."""
    run_data = _read_run_yaml(run_path, reader)
    return str(run_data.get("complexity_class", ""))


def _check_complexity_drift(
    run_data: dict[str, object],
    events: list[dict[str, object]],
    session_id: str | None = None,
) -> str | None:
    """Detect when actual work scope significantly exceeds the initial classification.

    Uses pre-read ``run_data`` and ``events`` (shared with other gate checks)
    so events.jsonl is read only once per delivery.

    Fires a WARNING (not a block) when:
      - ``complexity_class`` is ``MINIMAL``
      - actual file_modified count > REVIEW_SCOPE_FILE_THRESHOLD
      - actual count > COMPLEXITY_DRIFT_MULTIPLIER * planned files

    Returns:
        A warning string if complexity drift is detected, or None.
    """
    try:
        complexity_class = str(run_data.get("complexity_class", ""))
        if complexity_class != "MINIMAL":
            return None

        signals = run_data.get("complexity_signals")
        if not isinstance(signals, dict):
            return None
        planned_files = int(str(signals.get("files_affected", 0)))

        actual_files = _count_file_modified_current_session(events, session_id=session_id)

        if actual_files > REVIEW_SCOPE_FILE_THRESHOLD and actual_files > COMPLEXITY_DRIFT_MULTIPLIER * planned_files:
            logger.info(
                "complexity_drift_detected",
                complexity_class=complexity_class,
                planned_files=planned_files,
                actual_files=actual_files,
            )
            return (
                f"Complexity drift detected: classified MINIMAL "
                f"({planned_files} files planned) but {actual_files} files "
                f"were modified. Consider re-evaluating — tasks of this scope "
                f"typically require STANDARD complexity with mandatory REVIEW phase."
            )

    except Exception:  # justified: fail-open, complexity drift check must not block delivery
        logger.warning("complexity_drift_check_failed", exc_info=True)

    return None
