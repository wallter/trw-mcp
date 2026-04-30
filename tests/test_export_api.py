"""Tests for trw_mcp.export export-data behavior."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.export import export_data

from tests._test_export_support import _make_entry, _setup_project


class TestExportLearningsJson:
    """Tests for exporting learnings as JSON."""

    def test_exports_all_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Learning one")
        _make_entry(entries_dir, summary="Learning two")
        _make_entry(entries_dir, summary="Learning three")

        result = export_data(project, "learnings")
        assert result["status"] == "ok"
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 3

    def test_min_impact_filter(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="High impact", impact=0.9)
        _make_entry(entries_dir, summary="Low impact", impact=0.3)

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
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="CSV test entry", tags=["tag1", "tag2"])

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
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Metadata test")

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
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="CSV scope test")

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
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Count test one")
        _make_entry(entries_dir, summary="Count test two")

        result = export_data(project, "learnings")
        meta = result["metadata"]
        assert isinstance(meta, dict)
        assert meta["learnings_count"] == 2
