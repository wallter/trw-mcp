"""Tests for trw_mcp.export import behavior."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.export import import_learnings

from tests._test_export_support import _make_entry, _setup_project


class TestImportLearnings:
    """Tests for importing learnings from export files."""

    def test_basic_import(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")

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

        entries_dir = target / ".trw" / "learnings" / "entries"
        created = list(entries_dir.glob("*.yaml"))
        assert len(created) == 2

    def test_dedup_skips_similar(self, tmp_path: Path) -> None:
        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Pydantic v2 use_enum_values changes comparison semantics")

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
        source_data = [{"summary": "Dry run entry", "detail": "Test", "impact": 0.8}]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, dry_run=True)
        assert result["imported"] == 1
        assert result["dry_run"] is True

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
        assert result.get("error", "") != ""

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
        assert result.get("error", "") != ""

    def test_non_dict_entries_in_list_are_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in the source list are silently skipped."""
        target = _setup_project(tmp_path / "target")
        source_data = ["just a string", 42, {"summary": "Real entry", "detail": "D", "impact": 0.8}]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")
        result = import_learnings(source_file, target)
        assert result["imported"] == 1
        assert result["total_source"] == 3

    def test_entry_with_non_list_tags_skipped_by_tag_filter(self, tmp_path: Path) -> None:
        """Entry with tags that is not a list normalizes to empty set for filter."""
        target = _setup_project(tmp_path / "target")
        source_data = [{"summary": "Bad tags entry", "detail": "D", "impact": 0.8, "tags": "not-a-list"}]
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
        assert result.get("error", "") != ""
