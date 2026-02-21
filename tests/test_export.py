"""Tests for trw_mcp.export — cross-project export and import module."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.export import export_data, import_learnings
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
    _writer.write_yaml(entries_dir / filename, {
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
    })


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
