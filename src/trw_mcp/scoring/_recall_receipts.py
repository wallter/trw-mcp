"""Recall-tracking receipt row decoding.

Belongs to the ``scoring/_correlation.py`` facade. Re-exported there for
back-compat. These helpers decode a single ``recall_tracking.jsonl`` row into
the structured pieces :func:`trw_mcp.scoring._correlation.correlate_recalls`
needs -- recalled learning IDs and a receipt timestamp -- while failing open on
corrupt rows. Keeping the row-decoding mechanism here isolates it from the
correlation policy (windowing, recency discount, early-exit) that consumes it.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.scoring._utils import _ensure_utc

logger = structlog.get_logger(__name__)


def _extract_recalled_ids(record: dict[str, object]) -> list[str]:
    """Return learning IDs only for actual recall receipts.

    ``recall_tracking.jsonl`` mixes recall events and later outcome-only rows.
    Outcome rows must not be treated as fresh recall evidence for correlation.
    """
    matched_ids = record.get("matched_ids")
    if isinstance(matched_ids, list) and matched_ids:
        return [lid for lid in matched_ids if isinstance(lid, str) and lid]

    lid_single = record.get("learning_id")
    if not isinstance(lid_single, str) or not lid_single:
        return []

    outcome = record.get("outcome")
    if outcome not in (None, ""):
        return []

    return [lid_single]


def _parse_receipt_line(
    line: str,
    *,
    path: Path,
    line_number: int,
) -> dict[str, object] | None:
    """Decode one ``recall_tracking.jsonl`` line into a receipt record.

    Seam for :func:`correlate_recalls`: the Interface still fails open (a
    corrupt row is skipped, not raised), but the Implementation now preserves
    Locality by emitting a structured ``correlate_recalls.receipt_line_skipped``
    event when a row cannot be parsed. The raw line, matched IDs, and payload
    are deliberately NOT logged -- only ``path``, ``line_number``, and the
    ``error_class`` so a broken receipt log is operator-visible without leaking
    learning content.

    A row that parses to a non-object (bare JSON scalar/array) is also treated
    as corrupt -- otherwise the downstream ``record.get(...)`` would raise an
    uncaught ``AttributeError`` and erase ALL correlation for the session.

    Returns the decoded record, or ``None`` when the row is corrupt.
    """
    try:
        parsed: object = _json.loads(line)
        if not isinstance(parsed, dict):
            raise TypeError(f"expected JSON object, got {type(parsed).__name__}")
    except (ValueError, TypeError, _json.JSONDecodeError) as exc:
        logger.warning(
            "correlate_recalls.receipt_line_skipped",
            path=str(path),
            line_number=line_number,
            error_class=type(exc).__name__,
        )
        return None
    record: dict[str, object] = parsed
    return record


def _parse_receipt_timestamp(
    record: dict[str, object],
    *,
    path: Path,
    line_number: int,
) -> datetime | None:
    """Resolve a receipt timestamp from the ISO ``ts`` or epoch ``timestamp`` field.

    Seam for :func:`correlate_recalls`. Returns ``None`` for both a normal
    outcome-only row (no timestamp present) and an unparseable timestamp. The
    latter additionally emits a structured ``correlate_recalls.receipt_timestamp_invalid``
    event carrying ``path``, ``line_number``, the ``timestamp_field`` shape
    (``ts`` or ``timestamp``), and ``error_class`` -- never the learning IDs or
    record payload -- so a corrupt receipt clock is observable.
    """
    ts_str = str(record.get("ts", ""))
    if ts_str:
        try:
            return _ensure_utc(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
        except ValueError as exc:
            logger.warning(
                "correlate_recalls.receipt_timestamp_invalid",
                path=str(path),
                line_number=line_number,
                timestamp_field="ts",
                error_class=type(exc).__name__,
            )
            return None

    ts_raw = record.get("timestamp")
    if ts_raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(str(ts_raw)), tz=timezone.utc)
    except (ValueError, OSError) as exc:
        logger.warning(
            "correlate_recalls.receipt_timestamp_invalid",
            path=str(path),
            line_number=line_number,
            timestamp_field="timestamp",
            error_class=type(exc).__name__,
        )
        return None


__all__ = [
    "_extract_recalled_ids",
    "_parse_receipt_line",
    "_parse_receipt_timestamp",
]
