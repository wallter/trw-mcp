"""Tests for state/receipts.py — recall receipt management.

Covers log_recall_receipt and prune_recall_receipts at 60% -> target 90%.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.receipts import log_recall_receipt, prune_recall_receipts

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

    def test_shard_id_included_when_provided(self, tmp_project: Path) -> None:
        """Shard ID is included in the record when provided."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(
            trw_dir, query="test", matched_ids=["L-aaa"], shard_id="shard-01"
        )

        path = _receipt_path(trw_dir)
        record = json.loads(path.read_text().strip())
        assert record["shard_id"] == "shard-01"

    def test_shard_id_omitted_when_none(self, tmp_project: Path) -> None:
        """Shard ID is not present in record when not provided."""
        trw_dir = tmp_project / ".trw"
        log_recall_receipt(trw_dir, query="test", matched_ids=["L-aaa"], shard_id=None)

        path = _receipt_path(trw_dir)
        record = json.loads(path.read_text().strip())
        assert "shard_id" not in record

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


# ---------------------------------------------------------------------------
# TestPruneRecallReceipts
# ---------------------------------------------------------------------------


class TestPruneRecallReceipts:
    """Tests for prune_recall_receipts function."""

    def test_no_file_returns_zero(self, tmp_project: Path) -> None:
        """Returns 0 when receipt file does not exist."""
        trw_dir = tmp_project / ".trw"
        removed = prune_recall_receipts(trw_dir)
        assert removed == 0

    def test_under_limit_no_pruning(self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns 0 when records are within the limit."""
        trw_dir = tmp_project / ".trw"

        # Set a high limit so no pruning occurs
        config = TRWConfig(recall_receipt_max_entries=100)
        monkeypatch.setattr("trw_mcp.state.receipts.get_config", lambda: config)

        # Add 3 records
        for i in range(3):
            log_recall_receipt(trw_dir, query=f"q{i}", matched_ids=[f"L-{i:03d}"])

        removed = prune_recall_receipts(trw_dir)
        assert removed == 0

    def test_over_limit_prunes_oldest(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Prunes oldest records, keeping only the most recent `limit` entries."""
        trw_dir = tmp_project / ".trw"

        config = TRWConfig(recall_receipt_max_entries=3)
        monkeypatch.setattr("trw_mcp.state.receipts.get_config", lambda: config)

        # Add 5 records
        for i in range(5):
            log_recall_receipt(trw_dir, query=f"q{i}", matched_ids=[f"L-{i:03d}"])

        removed = prune_recall_receipts(trw_dir)
        assert removed == 2

        # Check that the file now has 3 records
        path = _receipt_path(trw_dir)
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert len(lines) == 3
        # The oldest (q0, q1) should be removed
        queries = [r["query"] for r in lines]
        assert "q0" not in queries
        assert "q1" not in queries
        assert "q2" in queries
        assert "q4" in queries

    def test_prune_at_exact_limit(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When records == limit, no pruning occurs."""
        trw_dir = tmp_project / ".trw"

        config = TRWConfig(recall_receipt_max_entries=3)
        monkeypatch.setattr("trw_mcp.state.receipts.get_config", lambda: config)

        for i in range(3):
            log_recall_receipt(trw_dir, query=f"q{i}", matched_ids=[f"L-{i:03d}"])

        removed = prune_recall_receipts(trw_dir)
        assert removed == 0

    def test_prune_limit_one_keeps_latest(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Limit of 1 keeps only the most recent record."""
        trw_dir = tmp_project / ".trw"

        config = TRWConfig(recall_receipt_max_entries=1)
        monkeypatch.setattr("trw_mcp.state.receipts.get_config", lambda: config)

        for i in range(4):
            log_recall_receipt(trw_dir, query=f"q{i}", matched_ids=[])

        removed = prune_recall_receipts(trw_dir)
        assert removed == 3

        path = _receipt_path(trw_dir)
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert len(lines) == 1
        assert lines[0]["query"] == "q3"
