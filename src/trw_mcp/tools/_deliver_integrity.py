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
from typing import cast

import structlog
from typing_extensions import TypedDict

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
        # PRD-DIST-432: use a direct read-only URI sqlite3.connect that
        # bypasses ``SQLiteBackend._connect`` (the singleton-aware path
        # that historically produced false-positive ``"file is not a
        # database"`` reports inside the MCP server's deliver flow —
        # see PRD-DIST-429 for the cycle-274..278 evidence). Read-only
        # mode does no WAL writes and no PRAGMA setup, so it can't
        # interact with active connection state. ``PRAGMA quick_check``
        # works identically in read-only mode.
        import sqlite3

        try:
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                rows = conn.execute("PRAGMA quick_check").fetchall()
            finally:
                conn.close()
            healthy = len(rows) == 1 and rows[0][0] == "ok"
            result["ok"] = healthy
            result["detail"] = rows[0][0] if rows else "empty"
        except sqlite3.DatabaseError as exc:
            # Genuine corruption surfaces here. Preserve the prior detail
            # shape so downstream consumers (reflog, dashboards) keep
            # working.
            logger.debug(
                "deliver_integrity_probe_failed",
                db=str(db_path),
                error=str(exc),
            )
            result["ok"] = False
            result["detail"] = str(exc)
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
