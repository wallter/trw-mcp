"""Recall tracking -- append-only exposure receipts.

Each **receipt** (``outcome: null``) says a learning was surfaced, and carries
the session join keys and the surface that showed it (PRD-FIX-144 FR01). No
row is ever rewritten. Logs from older versions may also hold **outcome rows**
(``outcome`` set); PRD-CORE-293 deleted their only writer, which had no caller,
along with the calibration reader, so readers must tolerate them but nothing
new produces them.

Rows carry ids, repo-relative paths, labels and timestamps only -- never
learning content (NFR04).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.state._helpers import rotate_jsonl
from trw_mcp.state._paths import resolve_trw_dir

logger = structlog.get_logger(__name__)

_TRACKING_FILE = "logs/recall_tracking.jsonl"

# PRD-FIX-085 FR04: rotate at 10 MB matching surface_tracking parity.
# Pre-fix this file grew unbounded -- observed 52 MB on the dev repo.
_ROTATION_THRESHOLD_BYTES = 10 * 1024 * 1024


def session_keys(trw_dir: Path) -> dict[str, str]:
    """Join keys every new receipt and outcome row carries (PRD-FIX-144 FR01).

    ``session_id`` is the identity surface rows use. ``process_session_id``
    stays stable when a run is pinned mid-session, which changes ``session_id``.
    """
    from trw_mcp.state._session_id import _get_process_session_id, resolve_effective_session_id

    return {
        "session_id": resolve_effective_session_id(trw_dir),
        "process_session_id": _get_process_session_id(),
    }


def _append_rows(trw_dir: Path, rows: list[dict[str, object]]) -> None:
    """Append *rows* under one advisory lock. Raises OSError; callers fail open.

    Written directly rather than through ``FileStateWriter``, which logs every
    failed append at error level before re-raising: a telemetry write failure
    is reported once, at debug, by the caller (PRD-FIX-144 NFR03).
    """
    tracking_path = trw_dir / _TRACKING_FILE
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    # PRD-FIX-085 FR04: rotate before append.
    rotate_jsonl(tracking_path, max_bytes=_ROTATION_THRESHOLD_BYTES)
    payload = "".join(json.dumps(row) + "\n" for row in rows)
    with tracking_path.open("a", encoding="utf-8") as fh:
        _lock_ex(fh.fileno())
        try:
            fh.write(payload)
            fh.flush()
        finally:
            _lock_un(fh.fileno())


def append_receipts(
    learning_ids: Iterable[str],
    query: str,
    *,
    surface: str,
    files_context: list[str] | None = None,
    trw_dir: Path | None = None,
) -> int:
    """Append one exposure receipt per id and return how many were written.

    Raises OSError on a failed write; :func:`record_recall` is the fail-open
    single-row form. ``files_context`` is written only when supplied (the
    before-edit surface), so recall receipts keep their key set.
    """
    ids = [lid for lid in learning_ids if lid]
    if not ids:
        return 0
    resolved = trw_dir or resolve_trw_dir()
    keys = session_keys(resolved)
    now = time.time()
    rows: list[dict[str, object]] = []
    for lid in ids:
        row: dict[str, object] = {"learning_id": lid, "query": query, "timestamp": now, "outcome": None}
        row.update(keys)
        row["surface"] = surface
        if files_context is not None:
            row["files_context"] = list(files_context)
        rows.append(row)
    _append_rows(resolved, rows)
    return len(rows)


def record_recall(learning_id: str, query: str, *, surface: str = "recall") -> bool:
    """Append one exposure receipt (``outcome: null``; never rewritten).

    Returns True on success, False on failure (fail-open).
    """
    try:
        append_receipts([learning_id], query, surface=surface)
        return True
    except Exception:  # trw-fail-silent-allow: exposure telemetry is fail-open (NFR03)
        logger.debug("recall_record_failed", learning_id=learning_id, exc_info=True)
        return False
