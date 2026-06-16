"""WAL health advisory helpers for memory connection status."""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _resolve_memory_db_path() -> Path:
    """Resolve the primary SQLite memory.db path from the .trw directory."""
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / "memory" / "memory.db"


def _append_wal_health(result: dict[str, object]) -> None:
    """Append WAL file size advisory to an embeddings status result dict."""
    try:
        from trw_mcp.models.config import get_config as _get_config

        cfg = _get_config()
        db_path = _resolve_memory_db_path()
        wal_path = db_path.with_suffix(".db-wal")
        if wal_path.exists():
            wal_size_mb = wal_path.stat().st_size / (1024 * 1024)
            if wal_size_mb > cfg.wal_checkpoint_threshold_mb:
                result["wal_size_mb"] = round(wal_size_mb, 1)
                result["wal_advisory"] = (
                    f"WAL file is {wal_size_mb:.1f}MB (threshold: {cfg.wal_checkpoint_threshold_mb}MB)"
                )
    except Exception:  # justified: fail-open, WAL health is advisory only
        logger.debug("wal_health_check_failed", exc_info=True)


__all__ = ["_append_wal_health", "_resolve_memory_db_path"]
