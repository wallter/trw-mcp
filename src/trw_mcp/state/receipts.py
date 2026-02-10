"""Recall receipt management — log, prune, serialize.

Extracted from tools/learning.py (PRD-FIX-010) to separate receipt
management from learning tool logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    json_serializer,
)

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()


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
    receipts_dir = trw_dir / _config.learnings_dir / _config.receipts_dir
    _writer.ensure_dir(receipts_dir)
    receipt_path = receipts_dir / "recall_log.jsonl"
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "matched_ids": matched_ids,
        "match_count": len(matched_ids),
    }
    if shard_id:
        record["shard_id"] = shard_id
    _writer.append_jsonl(receipt_path, record)


def prune_recall_receipts(trw_dir: Path) -> int:
    """Prune recall receipt log to keep only the most recent entries.

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Number of entries removed.
    """
    receipt_path = (
        trw_dir / _config.learnings_dir / _config.receipts_dir / "recall_log.jsonl"
    )
    if not receipt_path.exists():
        return 0

    records = _reader.read_jsonl(receipt_path)
    max_entries = _config.recall_receipt_max_entries

    if len(records) <= max_entries:
        return 0

    removed = len(records) - max_entries
    # Keep the most recent entries (last N)
    kept = records[-max_entries:]

    # Rewrite the file with only kept entries
    import json as _json

    receipt_path.write_text("", encoding="utf-8")
    for record in kept:
        line = _json.dumps(record, default=json_serializer) + "\n"
        with receipt_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    return removed
