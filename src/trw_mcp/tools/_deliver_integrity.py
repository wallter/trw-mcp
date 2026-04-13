"""Integrity-on-delivery helper (PRD-INFRA-067 / C2).

Wraps :meth:`trw_memory.storage.sqlite_backend.SQLiteBackend.check_integrity`
into a single-call helper that :func:`trw_deliver` invokes once per delivery.
Records the result in ``events.jsonl`` and returns it for inclusion in the
deliver response payload.

Observability ONLY. A failed integrity probe at deliver time is logged at
WARNING level with the detail; it NEVER raises, blocks, or triggers recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

import structlog

from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

__all__ = ["DeliverIntegrityResult", "check_memory_integrity_on_deliver"]

logger = structlog.get_logger(__name__)


class DeliverIntegrityResult(TypedDict):
    """Return shape for the deliver-time integrity probe."""

    ok: bool
    detail: str
    db_path: str
    checked_at: str


def check_memory_integrity_on_deliver(
    trw_dir: Path,
    run_dir: Path | None = None,
) -> DeliverIntegrityResult:
    """Run one ``PRAGMA quick_check`` on the memory DB and record the outcome.

    Args:
        trw_dir: Resolved ``.trw`` directory.
        run_dir: Optional active-run directory — when provided, the result is
            appended to ``run_dir/meta/events.jsonl`` under event name
            ``db_integrity_check_on_deliver``.

    Returns:
        Dict with ``ok``, ``detail``, ``db_path``, ``checked_at`` keys.
        Always returns — never raises. On unexpected errors sets ``ok=False``
        and ``detail`` to the error string.
    """
    db_path = trw_dir / "memory" / "memory.db"
    checked_at = datetime.now(timezone.utc).isoformat()
    result: DeliverIntegrityResult = {
        "ok": False,
        "detail": "unknown",
        "db_path": str(db_path),
        "checked_at": checked_at,
    }

    if not db_path.exists():
        # Missing DB at deliver time is not a corruption event — fresh runs
        # may not have materialised a memory DB yet.
        result["ok"] = True
        result["detail"] = "db_missing"
    else:
        try:
            from trw_memory.storage.sqlite_backend import SQLiteBackend

            raw = SQLiteBackend.check_integrity(db_path)
            result["ok"] = bool(raw.get("ok", False))
            result["detail"] = str(raw.get("detail", ""))
        except Exception as exc:  # justified: fail-open observability probe
            logger.debug(
                "deliver_integrity_probe_failed",
                db=str(db_path),
                error=str(exc),
            )
            result["ok"] = False
            result["detail"] = f"probe_error: {exc}"

    if result["ok"]:
        logger.debug(
            "deliver_db_integrity_ok",
            db=str(db_path),
            detail=result["detail"],
        )
    else:
        logger.warning(
            "deliver_db_integrity_regression",
            db=str(db_path),
            detail=result["detail"],
        )

    # Log to events.jsonl when a run is active.
    events_jsonl = run_dir / "meta" / "events.jsonl" if run_dir else None
    if events_jsonl is not None and events_jsonl.parent.exists():
        try:
            events_logger = FileEventLogger(FileStateWriter())
            events_logger.log_event(
                events_jsonl,
                "db_integrity_check_on_deliver",
                cast("dict[str, object]", dict(result)),
            )
        except Exception:  # justified: event logging must never break deliver
            logger.debug("deliver_integrity_event_log_failed", exc_info=True)

    return result
