"""Recall receipt management — log, prune, serialize.

Extracted from tools/learning.py (PRD-FIX-010) to separate receipt
management from learning tool logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    json_serializer,
)



def _receipt_path(trw_dir: Path) -> Path:
    """Return the path to the recall receipt log."""
    config = get_config()
    return trw_dir / config.learnings_dir / config.receipts_dir / "recall_log.jsonl"


def log_recall_receipt(
    trw_dir: Path,
    query: str,
    matched_ids: list[str],
    *,
    shard_id: str | None = None,
) -> None:
    """Append a recall receipt to .trw/learnings/receipts/recall_log.jsonl.

    Records which learnings were retrieved and when, enabling
    outcome correlation in Phase 1c.

    Args:
        trw_dir: Path to .trw directory.
        query: The recall query string.
        matched_ids: IDs of matched learning entries.
        shard_id: Optional shard identifier for sub-agent attribution.
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
    if shard_id:
        record["shard_id"] = shard_id
    writer.append_jsonl(path, record)


def prune_recall_receipts(trw_dir: Path) -> int:
    """Prune recall receipt log to keep only the most recent entries.

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Number of entries removed.
    """
    config = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()

    path = _receipt_path(trw_dir)
    if not path.exists():
        return 0

    records = reader.read_jsonl(path)
    limit = config.recall_receipt_max_entries

    if len(records) <= limit:
        return 0

    removed = len(records) - limit

    # Rewrite the file atomically (DEBT-028)
    content = "".join(
        json.dumps(record, default=json_serializer) + "\n"
        for record in records[-limit:]
    )
    writer.write_text(path, content)

    return removed
