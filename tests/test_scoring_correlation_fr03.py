"""Tests for PRD-FIX-053-FR03: Outcome correlation via SQLite instead of YAML scan.

Verifies:
1. SQLite path is used when memory_adapter.find_entry_by_id() returns a dict
2. YAML fallback runs when SQLite returns None
3. Neither path is used when both return None (entry skipped)
4. No YAML write-back when entry file does not exist on disk
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.scoring._correlation import process_outcome


def _write_receipt(trw_dir: Path, learning_id: str) -> None:
    """Write a single recall_tracking receipt for the given learning ID."""
    receipt_file = trw_dir / "logs" / "recall_tracking.jsonl"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(timezone.utc).timestamp()
    receipt_file.write_text(
        json.dumps({"timestamp": now_ts, "learning_id": learning_id}) + "\n"
    )


def _make_sqlite_data(learning_id: str) -> dict[str, object]:
    return {
        "id": learning_id,
        "summary": f"learning {learning_id}",
        "q_value": 0.5,
        "q_observations": 0,
        "recurrence": 1,
        "outcome_history": [],
    }


class TestProcessOutcomeSQLitePath:
    """FR03: SQLite path (O(1) lookup) is preferred over YAML scan."""

    def test_sqlite_path_used_when_available(self, tmp_path: Path) -> None:
        """When memory_adapter.find_entry_by_id returns data, it is used directly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _write_receipt(trw_dir, "test-lr-001")

        sqlite_data = _make_sqlite_data("test-lr-001")

        with (
            patch(
                "trw_mcp.state.memory_adapter.find_entry_by_id",
                return_value=sqlite_data,
            ) as mock_sqlite,
            patch(
                "trw_mcp.state.analytics.find_entry_by_id",
            ) as mock_yaml,
            patch("trw_mcp.state.persistence.FileStateWriter.write_yaml"),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed")

        assert "test-lr-001" in updated
        # SQLite must be called exactly once per unique learning ID
        assert mock_sqlite.call_count == 1, (
            f"SQLite lookup must be called exactly once, got {mock_sqlite.call_count}"
        )
        mock_sqlite.assert_called_once_with(trw_dir, "test-lr-001")
        # YAML fallback must NOT be called when SQLite succeeds
        mock_yaml.assert_not_called()

    def test_yaml_fallback_when_sqlite_returns_none(self, tmp_path: Path) -> None:
        """When SQLite returns None, YAML glob scan is attempted as fallback."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _write_receipt(trw_dir, "old-lr-002")
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        entry_path = entries_dir / "old-lr-002.yaml"
        yaml_data: dict[str, object] = {
            "id": "old-lr-002",
            "summary": "pre-migration learning",
            "q_value": 0.5,
            "q_observations": 0,
            "recurrence": 1,
            "outcome_history": [],
        }

        with (
            patch(
                "trw_mcp.state.memory_adapter.find_entry_by_id",
                return_value=None,
            ) as mock_sqlite,
            patch(
                "trw_mcp.state.analytics.find_entry_by_id",
                return_value=(entry_path, yaml_data),
            ) as mock_yaml,
            patch("trw_mcp.state.persistence.FileStateWriter.write_yaml"),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed")

        assert "old-lr-002" in updated
        # Both paths attempted: SQLite first, then YAML fallback
        mock_sqlite.assert_called_once_with(trw_dir, "old-lr-002")
        # YAML fallback must be called exactly once for the entry
        assert mock_yaml.call_count == 1, (
            f"YAML fallback must be called exactly once (not {mock_yaml.call_count}x) "
            "when SQLite returns None"
        )

    def test_entry_skipped_when_both_sources_return_none(self, tmp_path: Path) -> None:
        """When both SQLite and YAML return None, the entry is skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _write_receipt(trw_dir, "missing-lr-003")
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        with (
            patch(
                "trw_mcp.state.memory_adapter.find_entry_by_id",
                return_value=None,
            ),
            patch(
                "trw_mcp.state.analytics.find_entry_by_id",
                return_value=None,
            ),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed")

        assert updated == []

    def test_yaml_not_written_when_no_entry_path(self, tmp_path: Path) -> None:
        """When SQLite has data but no YAML file exists on disk, skip write-back."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _write_receipt(trw_dir, "sqlite-only-004")
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        # Intentionally do NOT create the .yaml file

        sqlite_data = _make_sqlite_data("sqlite-only-004")
        mock_writer = MagicMock()

        with (
            patch(
                "trw_mcp.state.memory_adapter.find_entry_by_id",
                return_value=sqlite_data,
            ),
            patch(
                "trw_mcp.state.persistence.FileStateWriter",
                return_value=mock_writer,
            ),
        ):
            updated = process_outcome(trw_dir, 0.5, "task_complete")

        assert "sqlite-only-004" in updated
        # No YAML write-back when the file does not exist on disk
        mock_writer.write_yaml.assert_not_called()

    def test_empty_correlated_returns_empty_list(self, tmp_path: Path) -> None:
        """When no recalls are found, process_outcome returns empty list immediately."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "logs").mkdir()
        # No recall_tracking.jsonl -> no correlated entries

        with patch(
            "trw_mcp.state.memory_adapter.find_entry_by_id",
        ) as mock_sqlite:
            updated = process_outcome(trw_dir, 0.8, "tests_passed")

        assert updated == []
        # SQLite not called if no correlated IDs — early return path
        mock_sqlite.assert_not_called()

    def test_q_value_updated_in_sqlite_data(self, tmp_path: Path) -> None:
        """Q-value in returned data is updated from its original value (not unchanged)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _write_receipt(trw_dir, "q-update-005")

        sqlite_data = _make_sqlite_data("q-update-005")
        original_q = float(str(sqlite_data["q_value"]))  # 0.5

        with (
            patch(
                "trw_mcp.state.memory_adapter.find_entry_by_id",
                return_value=sqlite_data,
            ),
            patch("trw_mcp.state.persistence.FileStateWriter.write_yaml"),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed")

        assert "q-update-005" in updated
        # Q-value must have been updated (positive reward → higher q_value)
        new_q = float(str(sqlite_data.get("q_value", original_q)))
        assert new_q != original_q, (
            f"Q-value must change after positive reward; still {new_q}"
        )
        assert new_q > original_q, (
            f"Positive reward must increase Q-value; {new_q} <= {original_q}"
        )
