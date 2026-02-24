"""Recall tracking — records when learnings are recalled and outcomes.

Supports PRD-CORE-034 outcome-based impact calibration by tracking
which learnings are recalled and whether they lead to successful outcomes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = logging.getLogger(__name__)

_TRACKING_FILE = "logs/recall_tracking.jsonl"


def record_recall(learning_id: str, query: str) -> bool:
    """Record that a learning was recalled during a session.

    Returns True on success, False on failure (fail-open).
    """
    try:
        trw_dir = resolve_trw_dir()
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        entry: dict[str, Any] = {
            "learning_id": learning_id,
            "query": query,
            "timestamp": time.time(),
            "outcome": None,  # filled in on deliver
        }
        writer.append_jsonl(tracking_path, entry)
        return True
    except Exception:
        logger.debug("Failed to record recall for %s", learning_id)
        return False


def record_outcome(learning_id: str, outcome: str) -> bool:
    """Record outcome for a previously recalled learning.

    outcome: "positive" (task succeeded), "negative" (task failed), "neutral"
    Returns True on success.
    """
    try:
        trw_dir = resolve_trw_dir()
        tracking_path = trw_dir / _TRACKING_FILE
        if not tracking_path.exists():
            return False

        writer = FileStateWriter()
        entry: dict[str, Any] = {
            "learning_id": learning_id,
            "outcome": outcome,
            "timestamp": time.time(),
        }
        writer.append_jsonl(tracking_path, entry)
        return True
    except Exception:
        logger.debug("Failed to record outcome for %s", learning_id)
        return False


def get_recall_stats(entries_dir: Path | None = None) -> dict[str, Any]:
    """Get recall statistics for outcome-based calibration.

    Returns:
        {total_recalls, unique_learnings, positive_outcomes, negative_outcomes, neutral_outcomes}
    """
    try:
        trw_dir = resolve_trw_dir()
        tracking_path = trw_dir / _TRACKING_FILE
        if not tracking_path.exists():
            return {
                "total_recalls": 0,
                "unique_learnings": 0,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
                "neutral_outcomes": 0,
            }

        reader = FileStateReader()
        records = reader.read_jsonl(tracking_path)

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
            total += 1

        return {
            "total_recalls": total,
            "unique_learnings": len(learning_ids),
            "positive_outcomes": positive,
            "negative_outcomes": negative,
            "neutral_outcomes": neutral,
        }
    except Exception:
        return {
            "total_recalls": 0,
            "unique_learnings": 0,
            "positive_outcomes": 0,
            "negative_outcomes": 0,
            "neutral_outcomes": 0,
        }
