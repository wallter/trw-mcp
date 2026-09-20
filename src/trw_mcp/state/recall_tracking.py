"""Recall tracking -- append-only exposure receipts and outcome rows.

Supports PRD-CORE-034 outcome-based impact calibration. The log holds two
kinds of row and no row is ever rewritten:

- a **receipt** (``outcome: null``) says a learning was surfaced, and carries
  the session join keys and the surface that showed it (PRD-FIX-144 FR01);
- an **outcome row** (``outcome`` set) records what is known about a
  learning's usefulness. Since R10 the only producer is explicit feedback
  (FR03); build and delivery results never become per-learning outcomes.

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
from trw_mcp.models.typed_dicts import RecallStats
from trw_mcp.state._helpers import read_jsonl_resilient, rotate_jsonl
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


def record_outcome(learning_id: str, outcome: str, *, source: str = "") -> bool:
    """Append an outcome row for *learning_id*, creating the log when absent.

    outcome: "positive", "negative" or "neutral". *source* names the signal,
    e.g. ``explicit_feedback`` from trw_learn_update. No matching receipt is
    looked up: the row does not claim the learning was exposed in this session.
    Returns True on success.
    """
    try:
        trw_dir = resolve_trw_dir()
        entry: dict[str, object] = {
            "learning_id": learning_id,
            "outcome": outcome,
            "timestamp": time.time(),
        }
        entry.update(session_keys(trw_dir))
        if source:
            entry["source"] = source
        _append_rows(trw_dir, [entry])
        return True
    except Exception:  # trw-fail-silent-allow: outcome telemetry is fail-open (NFR03)
        logger.debug("outcome_record_failed", learning_id=learning_id, exc_info=True)
        return False


def get_recall_stats(entries_dir: Path | None = None) -> RecallStats:
    """Get recall statistics for outcome-based calibration.

    ``total_recalls`` counts receipts only (``outcome`` null or empty); outcome
    rows feed the positive/negative/neutral tallies and never count as recalls
    (PRD-FIX-144 FR05).

    Returns:
        RecallStats with total_recalls, unique_learnings, positive_outcomes,
        negative_outcomes, and neutral_outcomes.
    """
    try:
        trw_dir = resolve_trw_dir()
        tracking_path = trw_dir / _TRACKING_FILE
        if not tracking_path.exists():
            return RecallStats(
                total_recalls=0,
                unique_learnings=0,
                positive_outcomes=0,
                negative_outcomes=0,
                neutral_outcomes=0,
            )

        # recall_tracking.jsonl is an append-only log written on every recall
        # and outcome, often by concurrent agents. A torn concurrent append (a
        # partial final line, or a row split mid multi-byte sequence) must drop
        # only that one row, not collapse the whole calibration aggregate to the
        # zeroed fallback via StateError. Use the resilient full-scan reader,
        # matching the precedent for events.jsonl in agent_work_evidence.py.
        records = read_jsonl_resilient(tracking_path)

        learning_ids: set[str] = set()
        positive = 0
        negative = 0
        neutral = 0
        total = 0

        for record in records:
            lid = str(record.get("learning_id", ""))
            if lid:
                learning_ids.add(lid)
            outcome = record.get("outcome")
            if outcome == "positive":
                positive += 1
            elif outcome == "negative":
                negative += 1
            elif outcome == "neutral":
                neutral += 1
            elif outcome is None or outcome == "":
                total += 1

        return RecallStats(
            total_recalls=total,
            unique_learnings=len(learning_ids),
            positive_outcomes=positive,
            negative_outcomes=negative,
            neutral_outcomes=neutral,
        )
    except Exception:  # justified: fail-open, stats computation failure returns zeroed defaults
        logger.warning("recall_stats_failed", exc_info=True)
        return RecallStats(
            total_recalls=0,
            unique_learnings=0,
            positive_outcomes=0,
            negative_outcomes=0,
            neutral_outcomes=0,
        )
