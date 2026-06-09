"""Recall-tracking correlation policy: windowing, recency discount, early-exit.

Belongs to the ``scoring/_correlation.py`` facade. Re-exported there (and from
``trw_mcp.scoring``) for back-compat.

This module owns the *policy* half of outcome correlation: given the
``recall_tracking.jsonl`` receipt log, decide which receipts fall inside the
correlation scope (session boundary or fixed window), compute a recency
discount for each, and emit the ``(learning_id, discount)`` tuples that
:func:`trw_mcp.scoring._correlation.process_outcome` consumes. The
*mechanism* half -- decoding a single receipt row -- lives in the sibling
``_recall_receipts`` module; keeping the two apart isolates the scan/window
policy from the row-decoding details it drives.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from trw_mcp.scoring._io_boundary import _find_session_start_ts as _io_find_session_start_ts
from trw_mcp.scoring._recall_receipts import (
    _extract_recalled_ids,
    _parse_receipt_line,
    _parse_receipt_timestamp,
)
from trw_mcp.scoring._utils import TRWConfig, get_config

logger = structlog.get_logger(__name__)


_CONSECUTIVE_OLD_EARLY_EXIT = 50
"""Number of consecutive out-of-window records before ``correlate_recalls``
stops scanning (PRD-FIX-070-FR06).  Allows for minor non-chronological
records while still providing early exit on chronological files."""


def _find_session_start_for_correlation(trw_dir: Path) -> datetime | None:
    """Resolve the session-start finder through the ``_correlation`` facade.

    ``correlate_recalls`` historically lived in ``_correlation.py`` and tests
    (plus downstream monkeypatches) patch
    ``trw_mcp.scoring._correlation._find_session_start_ts``.  Keep that seam
    alive after extracting the windowing implementation here: the Interface is
    still the facade, while this helper preserves Locality for the policy
    Implementation.
    """
    try:
        from trw_mcp.scoring import _correlation as correlation_facade
    except ImportError:
        return _io_find_session_start_ts(trw_dir)
    finder = getattr(correlation_facade, "_find_session_start_ts", _io_find_session_start_ts)
    return finder(trw_dir)


def correlate_recalls(
    trw_dir: Path,
    window_minutes: int,
    *,
    scope: str = "",
) -> list[tuple[str, float]]:
    """Find learning IDs from recent recall receipts within the correlation scope.

    PRD-CORE-026-FR04: Session-scoped correlation replaces the fixed 30-min
    window. When scope="session", correlates with ALL recall receipts since
    the last run_init/session_start event. Falls back to window-based when
    no session boundary is found.

    Returns (learning_id, recency_discount) tuples. Discount ranges from
    1.0 (just recalled) to 0.5 (at edge of window).

    Args:
        trw_dir: Path to .trw directory.
        window_minutes: How many minutes back to look for recall receipts
            (used when scope is "window" or as fallback).
        scope: Correlation scope -- "session" or "window". Empty string
            reads from config.

    Returns:
        List of (learning_id, discount) tuples. May contain duplicates
        across receipts (caller should deduplicate).
    """
    cfg_corr: TRWConfig = get_config()
    effective_scope = scope or cfg_corr.learning_outcome_correlation_scope
    receipt_path = trw_dir / "logs" / "recall_tracking.jsonl"
    if not receipt_path.exists():
        return []

    now = datetime.now(timezone.utc)

    # Determine the cutoff timestamp based on scope (session overrides window)
    cutoff_ts = now - timedelta(minutes=window_minutes)
    if effective_scope == "session":
        session_start = _find_session_start_for_correlation(trw_dir)
        if session_start is not None:
            cutoff_ts = session_start

    # Total seconds from cutoff to now (for discount calculation)
    total_window_secs = max((now - cutoff_ts).total_seconds(), 1.0)
    results: list[tuple[str, float]] = []

    # Read raw lines and iterate in reverse for early-exit optimization
    # (PRD-FIX-070-FR02/FR06). Since recall_tracking.jsonl is append-only
    # and chronological, once we hit a record older than the cutoff, ALL
    # remaining records are also older -- we can break.
    try:
        raw_lines = receipt_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError (a ValueError, not an OSError) fires when a
        # concurrent writer tears a multi-byte sequence at the tail of
        # recall_tracking.jsonl.  Both exceptions must be caught so that
        # correlate_recalls honors its fail-open contract and the deferred-
        # delivery outcome_correlation step never crashes the caller.
        logger.debug("recall_tracking_read_failed", exc_info=True)
        return []

    records_scanned = 0
    records_in_window = 0
    consecutive_old = 0

    # Track the original (1-based) line number while scanning in reverse so the
    # skipped-row observability events point at the right line in the file.
    total_lines = len(raw_lines)
    for offset, line in enumerate(reversed(raw_lines)):
        line_number = total_lines - offset
        stripped = line.strip()
        if not stripped:
            continue
        records_scanned += 1
        record = _parse_receipt_line(stripped, path=receipt_path, line_number=line_number)
        if record is None:
            continue

        # Extract timestamp (supports both ISO ``ts`` and epoch ``timestamp``).
        receipt_ts = _parse_receipt_timestamp(record, path=receipt_path, line_number=line_number)
        if receipt_ts is None:
            continue

        # Early exit: after enough consecutive old records, assume all
        # remaining are also old (file is mostly chronological).
        # PRD-FIX-070-FR06
        if receipt_ts < cutoff_ts:
            consecutive_old += 1
            if consecutive_old >= _CONSECUTIVE_OLD_EARLY_EXIT:
                break
            continue
        consecutive_old = 0

        elapsed_secs = (now - receipt_ts).total_seconds()
        if elapsed_secs < 0:
            continue

        discount = max(
            cfg_corr.scoring_recency_discount_floor,
            1.0 - elapsed_secs / total_window_secs,
        )

        recalled_ids = _extract_recalled_ids(record)
        if recalled_ids:
            results.extend((lid, discount) for lid in recalled_ids)
            records_in_window += 1

    logger.debug(
        "correlate_recalls_stats",
        total_lines=len(raw_lines),
        records_scanned=records_scanned,
        records_in_window=records_in_window,
        unique_ids=len({lid for lid, _ in results}),
    )

    return results


__all__ = [
    "_CONSECUTIVE_OLD_EARLY_EXIT",
    "correlate_recalls",
]
