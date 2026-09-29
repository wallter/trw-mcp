"""Recall receipt management — log, prune, serialize.

Extracted from tools/learning.py (PRD-FIX-010) to separate receipt
management from learning tool logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config

logger = structlog.get_logger(__name__)
from trw_mcp.state.persistence import (
    FileStateWriter,
)


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def _receipt_path(trw_dir: Path) -> Path:
    """Return the path to the recall receipt log."""
    config = get_config()
    return trw_dir / config.learnings_dir / config.receipts_dir / "recall_log.jsonl"


def log_recall_receipt(
    trw_dir: Path,
    query: str,
    matched_ids: list[str],
) -> None:
    """Append a recall receipt to .trw/learnings/receipts/recall_log.jsonl.

    Records which learnings were retrieved and when, enabling
    outcome correlation in Phase 1c.

    Args:
        trw_dir: Path to .trw directory.
        query: The recall query string.
        matched_ids: IDs of matched learning entries.
    """
    writer = FileStateWriter()
    path = _receipt_path(trw_dir)
    writer.ensure_dir(path.parent)
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "matched_ids": matched_ids,
        "match_count": len(matched_ids),
    }
    writer.append_jsonl(path, record)
