"""Tests for state/receipts.py — recall receipt management.

Covers log_recall_receipt and prune_recall_receipts at 60% -> target 90%.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.receipts import log_recall_receipt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _receipt_path(trw_dir: Path, config: TRWConfig | None = None) -> Path:
    from trw_mcp.models.config import get_config

    cfg = config or get_config()
    return trw_dir / cfg.learnings_dir / cfg.receipts_dir / "recall_log.jsonl"


# ---------------------------------------------------------------------------
# TestLogRecallReceipt
# ---------------------------------------------------------------------------


class TestLogRecallReceipt:
    """Tests for log_recall_receipt function."""

    def test_creates_receipt_file(self, tmp_project: Path) -> None:
        """Creates receipt file and directory if they don't exist."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="testing", matched_ids=["L-abc123"])

        path = _receipt_path(trw_dir)
        assert path.exists()

    def test_appends_record(self, tmp_project: Path) -> None:
        """Appended record contains required fields."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="my query", matched_ids=["L-aaa", "L-bbb"])

        path = _receipt_path(trw_dir)
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert len(lines) == 1
        record = lines[0]
        assert record["query"] == "my query"
        assert record["matched_ids"] == ["L-aaa", "L-bbb"]
        assert record["match_count"] == 2
        assert "ts" in record

    def test_multiple_appends(self, tmp_project: Path) -> None:
        """Multiple calls append multiple records."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="q1", matched_ids=["L-001"])
        log_recall_receipt(trw_dir, query="q2", matched_ids=["L-002", "L-003"])

        path = _receipt_path(trw_dir)
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert len(lines) == 2
        assert lines[0]["query"] == "q1"
        assert lines[1]["query"] == "q2"
        assert lines[1]["match_count"] == 2

    def test_call_signature_has_no_shard_id_parameter(self) -> None:
        """Regression: shard_id was a dormant, never-supplied parameter.

        Diagnostic finding item 3 (feedback-triage-framework-release-2026-09):
        the sole caller (`_session_recall_helpers.py`) never passed `shard_id`,
        so it was dropped rather than kept dormant.
        """
        import inspect

        params = inspect.signature(log_recall_receipt).parameters
        assert "shard_id" not in params

    def test_empty_matched_ids(self, tmp_project: Path) -> None:
        """Empty matched_ids results in match_count of 0."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="no results", matched_ids=[])

        path = _receipt_path(trw_dir)
        record = json.loads(path.read_text().strip())
        assert record["match_count"] == 0
        assert record["matched_ids"] == []

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Creates all parent directories if they don't exist."""
        trw_dir = tmp_path / ".trw"
        # No .trw structure created — should create on demand
        log_recall_receipt(trw_dir, query="test", matched_ids=["L-x"])

        path = _receipt_path(trw_dir)
        assert path.exists()

    def test_timestamp_is_iso_format(self, tmp_project: Path) -> None:
        """Timestamp field is a valid ISO format string."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="ts-check", matched_ids=["L-ts"])

        path = _receipt_path(trw_dir)
        record = json.loads(path.read_text().strip())
        from datetime import datetime

        # Should not raise
        dt = datetime.fromisoformat(record["ts"])
        assert dt.tzinfo is not None  # timezone-aware
