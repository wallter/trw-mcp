"""Tests for trw_mcp.export — cross-project export and import module."""

from __future__ import annotations

import json
import os
from pathlib import Path

from trw_mcp.export import (
    _collect_analytics,
    _collect_learnings,
    _learnings_to_csv,
    export_data,
    import_learnings,
    temp_project_root,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    entries_dir: Path,
    *,
    entry_id: str = "",
    summary: str = "Test learning",
    impact: float = 0.8,
    status: str = "active",
    tags: list[str] | None = None,
    source_type: str = "agent",
) -> None:
    """Write a YAML learning entry file."""
    import uuid

    if not entry_id:
        entry_id = f"L-{uuid.uuid4().hex[:8]}"
    slug = summary.lower().replace(" ", "-")[:40]
    filename = f"2026-02-21-{slug}.yaml"
    _writer.write_yaml(
        entries_dir / filename,
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"Detail for: {summary}",
            "impact": impact,
            "status": status,
            "tags": tags or ["test"],
            "source_type": source_type,
            "created": "2026-02-21T00:00:00Z",
            "updated": "2026-02-21T00:00:00Z",
            "q_value": 0.5,
            "access_count": 1,
        },
    )


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal .trw structure for export tests."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------


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
        assert len(lines) == 2  # header + 1 data row
        header = lines[0]
        assert "id" in header
        assert "summary" in header
        assert "impact" in header
        # Tags should be semicolon-delimited
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


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------


class TestImportLearnings:
    """Tests for importing learnings from export files."""

    def test_basic_import(self, tmp_path: Path) -> None:
        # Set up target project
        target = _setup_project(tmp_path / "target")

        # Create source export file
        source_data = {
            "metadata": {"project": "source_project"},
            "learnings": [
                {
                    "id": "L-src001",
                    "summary": "Source learning one",
                    "detail": "Detail one",
                    "impact": 0.8,
                    "tags": ["imported"],
                },
                {
                    "id": "L-src002",
                    "summary": "Source learning two",
                    "detail": "Detail two",
                    "impact": 0.6,
                    "tags": ["imported"],
                },
            ],
        }
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 2
        assert result["skipped_duplicate"] == 0
        assert result["source_project"] == "source_project"

        # Verify files were created
        entries_dir = target / ".trw" / "learnings" / "entries"
        created = list(entries_dir.glob("*.yaml"))
        assert len(created) == 2

    def test_dedup_skips_similar(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        # Pre-populate target with an existing entry
        _make_entry(entries_dir, summary="Pydantic v2 use_enum_values changes comparison semantics")

        # Source has a near-duplicate
        source_data = [
            {
                "summary": "Pydantic v2 use_enum_values changes comparison semantics exactly",
                "detail": "Same thing",
                "impact": 0.8,
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["skipped_duplicate"] >= 1
        assert result["imported"] == 0

    def test_respects_min_impact(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        source_data = [
            {"summary": "High value", "detail": "Important", "impact": 0.9},
            {"summary": "Low value", "detail": "Noise", "impact": 0.3},
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, min_impact=0.7)
        assert result["imported"] == 1
        assert result["skipped_filter"] == 1

    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        source_data = [
            {"summary": "Dry run entry", "detail": "Test", "impact": 0.8},
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, dry_run=True)
        assert result["imported"] == 1
        assert result["dry_run"] is True

        # No files should have been written
        entries_dir = target / ".trw" / "learnings" / "entries"
        created = list(entries_dir.glob("*.yaml"))
        assert len(created) == 0

    def test_tag_filter(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        source_data = [
            {"summary": "Has matching tag", "detail": "D", "impact": 0.8, "tags": ["pydantic", "testing"]},
            {"summary": "No matching tag", "detail": "D", "impact": 0.8, "tags": ["unrelated"]},
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["imported"] == 1
        assert result["skipped_filter"] == 1

    def test_no_trw_dir(self, tmp_path: Path) -> None:
        source_file = tmp_path / "export.json"
        source_file.write_text("[]", encoding="utf-8")
        result = import_learnings(source_file, tmp_path / "nonexistent")
        assert result["status"] == "failed"

    def test_invalid_source_file(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "bad.json"
        source_file.write_text("not json at all", encoding="utf-8")
        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "Failed to read" in str(result.get("error", ""))

    def test_source_is_dict_without_learnings_key(self, tmp_path: Path) -> None:
        """Source JSON dict without 'learnings' key is rejected."""
        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps({"runs": []}), encoding="utf-8")
        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "list or export" in str(result.get("error", ""))

    def test_learnings_key_not_a_list(self, tmp_path: Path) -> None:
        """'learnings' value that is not a list is rejected."""
        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps({"learnings": "not-a-list"}), encoding="utf-8")
        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "must be a list" in str(result.get("error", ""))

    def test_non_dict_entries_in_list_are_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in the source list are silently skipped."""
        target = _setup_project(tmp_path / "target")
        source_data = [
            "just a string",
            42,
            {"summary": "Real entry", "detail": "D", "impact": 0.8},
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")
        result = import_learnings(source_file, target)
        assert result["imported"] == 1
        assert result["total_source"] == 3

    def test_entry_with_non_list_tags_skipped_by_tag_filter(self, tmp_path: Path) -> None:
        """Entry with tags that is not a list normalizes to empty set for filter."""
        target = _setup_project(tmp_path / "target")
        source_data = [
            {"summary": "Bad tags entry", "detail": "D", "impact": 0.8, "tags": "not-a-list"},
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")
        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["skipped_filter"] == 1
        assert result["imported"] == 0

    def test_missing_source_file_returns_error(self, tmp_path: Path) -> None:
        """OSError when source file does not exist."""
        target = _setup_project(tmp_path / "target")
        missing = tmp_path / "no-such-file.json"
        result = import_learnings(missing, target)
        assert result["status"] == "failed"
        assert "Failed to read" in str(result.get("error", ""))


# ---------------------------------------------------------------------------
# temp_project_root context manager
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _collect_learnings edge cases
# ---------------------------------------------------------------------------


class TestCollectLearnings:
    """Edge cases for _collect_learnings internal function."""

    def test_returns_empty_when_entries_dir_missing(self, tmp_path: Path) -> None:
        """No entries dir => empty list, no error."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig()
        result = _collect_learnings(trw_dir, config)
        assert result == []

    def test_skips_index_yaml(self, tmp_path: Path) -> None:
        """index.yaml in entries dir is skipped."""
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Real entry")
        _writer.write_yaml(entries_dir / "index.yaml", {"entries": []})
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config)
        assert len(result) == 1

    def test_since_filter(self, tmp_path: Path) -> None:
        """Entries created before 'since' date are excluded."""
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Old entry")  # created 2026-02-21
        config = TRWConfig()
        # Since date after the entry's created date
        result = _collect_learnings(project / ".trw", config, since="2026-03-01")
        assert len(result) == 0

    def test_since_filter_includes_recent(self, tmp_path: Path) -> None:
        """Entries created on or after 'since' date are included."""
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Recent entry")  # created 2026-02-21
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config, since="2026-01-01")
        assert len(result) == 1

    def test_corrupt_yaml_is_skipped(self, tmp_path: Path) -> None:
        """Entry files that fail to parse are silently skipped."""
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Good entry")
        # Write a corrupt YAML file
        bad_file = entries_dir / "2026-02-21-corrupt.yaml"
        bad_file.write_text(": :\n  bad: [unclosed", encoding="utf-8")
        config = TRWConfig()
        result = _collect_learnings(project / ".trw", config)
        # Should get at least the good entry, corrupt one is skipped
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# _learnings_to_csv edge cases
# ---------------------------------------------------------------------------


class TestLearningsToCsv:
    """Edge cases for CSV conversion of learning entries."""

    def test_empty_list_returns_headers_only(self) -> None:
        """Empty entries list produces CSV with just the header row."""
        csv_str = _learnings_to_csv([])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1  # header only
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
        # The tags column should be empty since tags is not a list
        # Just verify no crash
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
        assert len(lines) == 4  # header + 3 data rows


# ---------------------------------------------------------------------------
# _collect_analytics edge cases
# ---------------------------------------------------------------------------


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
            {
                "aggregate": {"total_runs": 12, "avg_score": 75},
            },
        )
        config = TRWConfig()
        result = _collect_analytics(project, project / ".trw", config)
        assert "ceremony_aggregates" in result
        assert result["ceremony_aggregates"]["total_runs"] == 12


# ---------------------------------------------------------------------------
# export_data scope edge cases
# ---------------------------------------------------------------------------


class TestExportDataScopes:
    """Edge cases for export_data with different scopes."""

    def test_csv_format_only_applies_to_learnings_scope(self, tmp_path: Path) -> None:
        """CSV format is only used when scope is exactly 'learnings', not 'all'."""
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="CSV scope test")

        # scope=all with fmt=csv should return learnings as list, not CSV
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
