"""Tests for trw_mcp.export export-data behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._test_export_support import _setup_project, _store_entry
from trw_mcp.export import export_data


@pytest.fixture(autouse=True)
def _route_memory(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Export reads ``selected_store``; the fake is this checkout's store (PRD-CORE-280 e1)."""
    return fake_memory_store


class TestExportLearningsJson:
    """Tests for exporting learnings as JSON."""

    def test_exports_all_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Learning one")
        _store_entry(project / ".trw", summary="Learning two")
        _store_entry(project / ".trw", summary="Learning three")

        result = export_data(project, "learnings")
        assert result["status"] == "ok"
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 3

    def test_min_impact_filter(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="High impact", impact=0.9)
        _store_entry(project / ".trw", summary="Low impact", impact=0.3)

        result = export_data(project, "learnings", min_impact=0.7)
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1
        assert learnings[0]["summary"] == "High impact"

    def test_no_trw_dir(self, tmp_path: Path) -> None:
        result = export_data(tmp_path, "learnings")
        assert result["status"] == "failed"
        assert "No .trw directory" in str(result.get("error", ""))


class TestExportLearningsCsv:
    """Tests for CSV export format."""

    def test_csv_has_headers_and_data(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="CSV test entry", tags=["tag1", "tag2"])

        result = export_data(project, "learnings", fmt="csv")
        csv_str = result.get("learnings_csv")
        assert isinstance(csv_str, str)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2
        header = lines[0]
        assert "id" in header
        assert "summary" in header
        assert "impact" in header
        assert "tag1;tag2" in lines[1]


class TestExportMetadata:
    """Tests for export metadata envelope."""

    def test_all_scope_has_metadata(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Metadata test")

        result = export_data(project, "all")
        assert result["status"] == "ok"
        meta = result.get("metadata")
        assert isinstance(meta, dict)
        assert "project" in meta
        assert "export_date" in meta
        assert "trw_version" in meta
        assert meta["scope"] == "all"


class TestExportDataScopes:
    """Edge cases for export_data with different scopes."""

    def test_csv_format_only_applies_to_learnings_scope(self, tmp_path: Path) -> None:
        """CSV format is only used when scope is exactly 'learnings', not 'all'."""
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="CSV scope test")

        result = export_data(project, "all", fmt="csv")
        assert "learnings" in result
        assert "learnings_csv" not in result

    def test_analytics_scope(self, tmp_path: Path) -> None:
        """scope='analytics' returns analytics data."""
        project = _setup_project(tmp_path)
        result = export_data(project, "analytics")
        assert result["status"] == "ok"
        assert "analytics" in result

    def test_metadata_includes_learnings_count(self, tmp_path: Path) -> None:
        """Metadata includes learnings_count when scope includes learnings."""
        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Count test one")
        _store_entry(project / ".trw", summary="Count test two")

        result = export_data(project, "learnings")
        meta = result["metadata"]
        assert isinstance(meta, dict)
        assert meta["learnings_count"] == 2
