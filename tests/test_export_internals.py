"""Tests for trw_mcp.export helper functions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._test_export_support import _setup_project, _store_entry, _writer
from trw_mcp.export import (
    _collect_analytics,
    _collect_learnings,
    _learnings_to_csv,
    temp_project_root,
)
from trw_mcp.models.config import TRWConfig


@pytest.fixture(autouse=True)
def _route_memory(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Export reads ``selected_store``; the fake is this checkout's store (PRD-CORE-280 e1)."""
    return fake_memory_store


class TestTempProjectRoot:
    """Edge cases for the temp_project_root context manager."""

    def test_restores_previous_root(self, tmp_path: Path, monkeypatch: object) -> None:
        """When TRW_PROJECT_ROOT was set before, it is restored on exit."""
        import os as _os

        _os.environ["TRW_PROJECT_ROOT"] = "/original/path"
        try:
            with temp_project_root(tmp_path):
                assert _os.environ["TRW_PROJECT_ROOT"] == str(tmp_path)
            assert _os.environ["TRW_PROJECT_ROOT"] == "/original/path"
        finally:
            _os.environ.pop("TRW_PROJECT_ROOT", None)

    def test_removes_root_when_not_set_before(self, tmp_path: Path) -> None:
        """When TRW_PROJECT_ROOT was not set, it is removed on exit."""
        os.environ.pop("TRW_PROJECT_ROOT", None)
        with temp_project_root(tmp_path):
            assert os.environ["TRW_PROJECT_ROOT"] == str(tmp_path)
        assert "TRW_PROJECT_ROOT" not in os.environ


class TestCollectLearnings:
    """Edge cases for _collect_learnings (PRD-CORE-280 FR05: reads the store, not YAML)."""

    def test_returns_empty_when_store_is_empty(self, tmp_path: Path) -> None:
        """No rows in this checkout's store => empty list, no error."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig()
        result = _collect_learnings(trw_dir, config)
        assert result == []

    def test_a_row_with_no_yaml_file_is_still_exported(self, tmp_path: Path) -> None:
        """A store-only row (never written to the retired YAML mirror) is exported."""
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Store-only entry")
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config)
        assert len(result) == 1
        assert result[0]["summary"] == "Store-only entry"

    def test_since_filter_excludes_everything_before_the_far_future(self, tmp_path: Path) -> None:
        """A row created just now is excluded once 'since' is set past its creation date."""
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Old entry")
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config, since="2999-01-01")
        assert len(result) == 0

    def test_since_filter_includes_recent(self, tmp_path: Path) -> None:
        """A row created just now is included once 'since' is set well before its creation date."""
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Recent entry")
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config, since="2000-01-01")
        assert len(result) == 1


class TestLearningsToCsv:
    """Edge cases for CSV conversion of learning entries."""

    def test_empty_list_returns_headers_only(self) -> None:
        """Empty entries list produces CSV with just the header row."""
        csv_str = _learnings_to_csv([])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1
        assert "id" in lines[0]
        assert "summary" in lines[0]

    def test_entry_with_missing_fields(self) -> None:
        """Entries missing optional fields produce empty CSV cells."""
        entries = [{"summary": "Minimal entry"}]
        csv_str = _learnings_to_csv(entries)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2
        assert "Minimal entry" in lines[1]

    def test_tags_non_list_produces_empty_string(self) -> None:
        """Non-list tags field results in empty tags cell."""
        entries = [{"summary": "Bad tags", "tags": "not-a-list"}]
        csv_str = _learnings_to_csv(entries)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2
        assert "Bad tags" in lines[1]

    def test_multiple_entries_all_present(self) -> None:
        """Multiple entries produce correct number of data rows."""
        entries = [
            {"id": "L1", "summary": "First", "impact": 0.8, "tags": ["a"]},
            {"id": "L2", "summary": "Second", "impact": 0.3, "tags": ["b", "c"]},
            {"id": "L3", "summary": "Third", "impact": 0.5, "tags": []},
        ]
        csv_str = _learnings_to_csv(entries)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 4


class TestCollectAnalytics:
    """Edge cases for analytics collection."""

    def test_no_analytics_yaml(self, tmp_path: Path) -> None:
        """Missing analytics.yaml returns dict without session_analytics."""
        project = _setup_project(tmp_path)
        config = TRWConfig()
        result = _collect_analytics(project, project / ".trw", config)
        assert "session_analytics" not in result

    def test_analytics_yaml_loaded(self, tmp_path: Path) -> None:
        """Existing analytics.yaml is loaded into session_analytics."""
        project = _setup_project(tmp_path)
        context_dir = project / ".trw" / "context"
        _writer.write_yaml(context_dir / "analytics.yaml", {"sessions": 5, "learnings_total": 10})
        config = TRWConfig()
        result = _collect_analytics(project, project / ".trw", config)
        assert "session_analytics" in result
        analytics = result["session_analytics"]
        assert isinstance(analytics, dict)
        assert analytics["sessions"] == 5

    def test_report_yaml_loaded(self, tmp_path: Path) -> None:
        """analytics-report.yaml with aggregate key populates ceremony_aggregates."""
        project = _setup_project(tmp_path)
        context_dir = project / ".trw" / "context"
        _writer.write_yaml(
            context_dir / "analytics-report.yaml",
            {"aggregate": {"total_runs": 12, "avg_score": 75}},
        )
        config = TRWConfig()
        result = _collect_analytics(project, project / ".trw", config)
        assert "ceremony_aggregates" in result
        assert result["ceremony_aggregates"]["total_runs"] == 12
