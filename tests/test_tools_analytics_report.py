"""Timestamp, tool-layer, and integration tests for analytics tools."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from tests._test_tools_analytics_support import _write_run, writer  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics.report import _parse_run_id_timestamp, scan_all_runs
from trw_mcp.state.persistence import FileStateWriter


class TestParseRunIdTimestamp:
    """Edge cases for _parse_run_id_timestamp."""

    def test_standard_format_parses_correctly(self) -> None:
        """Standard run_id format returns correct ISO timestamp."""
        result = _parse_run_id_timestamp("20260220T120000Z-abcd1234")
        assert result == "2026-02-20T12:00:00+00:00"

    def test_midnight_timestamp(self) -> None:
        """Midnight timestamp parses correctly."""
        result = _parse_run_id_timestamp("20260101T000000Z-aaaa1111")
        assert result == "2026-01-01T00:00:00+00:00"

    def test_invalid_run_id_returns_raw(self) -> None:
        """Unparseable run_id is returned as-is."""
        raw = "not-a-valid-run-id"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string (fallback path)."""
        result = _parse_run_id_timestamp("")
        assert result == ""

    def test_short_ts_part_returns_raw(self) -> None:
        """Run_id with a short timestamp part (< 16 chars) returns as-is."""
        raw = "2026-abcd"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_no_hyphen_separator_returns_raw(self) -> None:
        """Run_id with no hyphen returns as-is (split gives only one part)."""
        raw = "20260220T120000Z"
        result = _parse_run_id_timestamp(raw)
        assert result == "2026-02-20T12:00:00+00:00"

    def test_bad_date_digits_returns_raw(self) -> None:
        """Invalid month/day digits return the raw run_id."""
        raw = "20261399T000000Z-bad00000"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_different_valid_dates(self) -> None:
        """Various valid dates parse to correct ISO strings."""
        cases = [
            ("20260630T235959Z-xxxx0000", "2026-06-30T23:59:59+00:00"),
            ("20260101T120000Z-yyyy1111", "2026-01-01T12:00:00+00:00"),
        ]
        for run_id, expected in cases:
            assert _parse_run_id_timestamp(run_id) == expected, f"Failed for {run_id}"


class TestAnalyticsIntegration:
    """Integration tests covering cache write (T-20) and since validation."""

    def test_t20_cache_file_written(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-20: scan_all_runs writes cache to .trw/context/analytics-report.yaml."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: TRWConfig(runs_root=".trw/runs"))
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: trw_dir)

        _write_run(
            writer,
            tmp_path,
            "cache-task",
            "20260101T000000Z-cache000",
            events=[{"event": "session_start"}],
        )

        scan_all_runs()

        cache_path = trw_dir / "context" / "analytics-report.yaml"
        assert cache_path.exists(), "Cache file not written"

        from trw_mcp.state.persistence import FileStateReader

        cached = FileStateReader().read_yaml(cache_path)
        assert cached["runs_scanned"] == 1

    def test_since_malformed_reports_parse_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed since filter surfaces in parse_errors rather than crashing."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: TRWConfig(runs_root=".trw/runs"))
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        _write_run(
            writer,
            tmp_path,
            "task-a",
            "20260101T000000Z-aaaa1111",
            events=[{"event": "session_start"}],
        )

        result = scan_all_runs(since="not-a-date")
        assert any("not a valid ISO date" in str(e) for e in result["parse_errors"])
