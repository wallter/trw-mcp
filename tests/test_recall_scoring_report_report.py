"""Split report/helper coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state._helpers import safe_float
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.report import (
    _date_in_range,
    _parse_date,
    _ts_diff_seconds,
    assemble_report,
    compute_learning_yield,
)


class TestTsDiffSecondsException:
    """Cover _ts_diff_seconds invalid timestamp handling."""

    def test_invalid_start_returns_none(self) -> None:
        """Non-parseable start timestamp returns None."""
        result = _ts_diff_seconds("not-a-timestamp", "2026-02-19T10:00:00Z")
        assert result is None

    def test_invalid_end_returns_none(self) -> None:
        """Non-parseable end timestamp returns None."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "also-invalid")
        assert result is None

    def test_both_invalid_returns_none(self) -> None:
        """Both timestamps invalid returns None."""
        result = _ts_diff_seconds("", "")
        assert result is None

    def test_valid_timestamps_return_seconds(self) -> None:
        """Sanity check: valid timestamps return correct elapsed seconds."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "2026-02-19T11:00:00Z")
        assert result == 3600.0


class TestComputeLearningYieldSQLiteFailure:
    """Cover exception handling when list_active_learnings raises."""

    def test_sqlite_error_returns_empty_summary(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When list_active_learnings raises, return empty LearningSummary."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch("trw_mcp.state.report.list_active_learnings", side_effect=RuntimeError("db corrupt")):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0
        assert result.avg_impact == 0.0


class TestParseDateInvalid:
    """Cover _parse_date invalid input handling."""

    def test_invalid_timestamp_returns_none(self) -> None:
        """Non-ISO timestamp returns None (lines 197-198)."""
        result = _parse_date("not-a-date")
        assert result is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None (early return)."""
        result = _parse_date(None)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns None (early return)."""
        result = _parse_date("")
        assert result is None

    def test_valid_iso_timestamp_extracts_date(self) -> None:
        """Valid ISO timestamp extracts YYYY-MM-DD."""
        result = _parse_date("2026-02-19T10:30:00Z")
        assert result == "2026-02-19"


class TestDateInRangeEmptyCreated:
    """Cover _date_in_range empty created handling."""

    def test_empty_created_returns_false(self) -> None:
        """Empty created string returns False immediately (line 204)."""
        result = _date_in_range("", "2026-02-01", "2026-02-28")
        assert result is False

    def test_in_range_date_returns_true(self) -> None:
        """Date within range returns True."""
        result = _date_in_range("2026-02-15", "2026-02-01", "2026-02-28")
        assert result is True

    def test_before_range_returns_false(self) -> None:
        """Date before range returns False."""
        result = _date_in_range("2026-01-15", "2026-02-01", "2026-02-28")
        assert result is False

    def test_after_range_returns_false(self) -> None:
        """Date after range returns False."""
        result = _date_in_range("2026-03-01", "2026-02-01", "2026-02-28")
        assert result is False


class TestSafeFloat:
    """Tests for safe_float from _helpers.py."""

    def test_string_non_numeric_returns_default(self) -> None:
        """Non-numeric string value returns default."""
        assert safe_float({"k": "not-a-number"}, "k", 0.0) == 0.0

    def test_none_value_returns_default(self) -> None:
        """None value returns default."""
        assert safe_float({"k": None}, "k", 0.0) == 0.0

    def test_list_value_returns_default(self) -> None:
        """List value returns default."""
        assert safe_float({"k": [1, 2, 3]}, "k", 0.0) == 0.0

    def test_int_converts_correctly(self) -> None:
        """Integer value converts to float correctly."""
        assert safe_float({"k": 42}, "k", 0.0) == pytest.approx(42.0)

    def test_float_passthrough(self) -> None:
        """Float value passes through unchanged."""
        assert safe_float({"k": 0.75}, "k", 0.0) == pytest.approx(0.75)

    def test_numeric_string_converts(self) -> None:
        """Numeric string converts to float."""
        assert safe_float({"k": "0.85"}, "k", 0.0) == pytest.approx(0.85)

    def test_missing_key_returns_default(self) -> None:
        """Missing key returns default value."""
        assert safe_float({}, "missing", 0.5) == pytest.approx(0.5)


class TestAssembleReportBuildStatusException:
    """Cover build-status.yaml read exception in assemble_report."""

    def test_corrupt_build_status_results_in_none_build(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When build-status.yaml exists but raises on read, build=None (lines 271-272)."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260101T000000Z-aaaa0001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)

        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260101T000000Z-aaaa0001",
                "task": "task",
                "status": "active",
                "phase": "research",
            },
        )

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "test_count": "not-an-int",
            },
        )

        original_read = reader.read_yaml

        def selective_read(path: Path) -> dict[str, object]:
            if "build-status" in str(path):
                raise StateError("corrupt build status", path=str(path))
            return original_read(path)

        reader.read_yaml = selective_read  # type: ignore[method-assign]

        report = assemble_report(run_dir, reader, trw_dir)
        assert report.build is None
