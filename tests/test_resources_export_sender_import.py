from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from tests._resources_export_sender_support import _setup_project, _writer


class TestImportSourceValidation:
    """Lines 258, 261 — invalid source file shape."""

    def test_source_is_invalid_dict_no_learnings_key(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps({"not_learnings": "value"}), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "learnings" in str(result.get("error", "")).lower()

    def test_source_learnings_key_is_not_a_list(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps({"learnings": "should be a list"}), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "list" in str(result.get("error", "")).lower()


class TestImportDeduplicationEdgeCases:
    """Lines 269, 273-274 — dedup loop edge cases."""

    def test_index_yaml_skipped_during_dedup_load(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        _writer.write_yaml(
            entries_dir / "index.yaml",
            {"id": "INDEX", "summary": "Index file content"},
        )

        source_data = [
            {
                "summary": "New unique learning",
                "detail": "Some detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 1

    def test_unreadable_existing_entry_skipped_during_dedup(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        bad = entries_dir / "2026-02-21-corrupt.yaml"
        bad.write_text("!!python/object:os.system [ls]", encoding="utf-8")

        source_data = [
            {
                "summary": "Learning that should import fine",
                "detail": "Detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 1


class TestImportTagFilterNonList:
    """Lines 283, 295 — tag filter when entry_tags is not a list."""

    def test_non_list_tags_in_source_entry_treated_as_no_tags(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry with non-list tags",
                "detail": "Detail",
                "impact": 0.8,
                "tags": "not-a-list",
            },
            {
                "summary": "Entry with matching tag",
                "detail": "Detail",
                "impact": 0.8,
                "tags": ["pydantic"],
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["status"] == "ok"
        assert result["skipped_filter"] == 1
        assert result["imported"] == 1

    def test_none_tags_in_source_entry_treated_as_no_tags(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry with null tags",
                "detail": "Detail",
                "impact": 0.8,
                "tags": None,
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["status"] == "ok"
        assert result["skipped_filter"] == 1
        assert result["imported"] == 0


class TestImportResyncEnvRestore:
    """Line 347 — TRW_PROJECT_ROOT restored after resync when previously set."""

    def test_env_restored_after_resync_when_previously_set(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry to trigger resync",
                "detail": "Detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        original_val = "pre_import_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch("trw_mcp.export.resync_learning_index"):
                result = import_learnings(source_file, target)
            assert result["status"] == "ok"
            assert result["imported"] == 1
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]


class TestImportNonDictEntry:
    """Line 283 — non-dict entries in source list are silently skipped."""

    def test_non_dict_entry_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            "this is a string not a dict",
            42,
            None,
            {
                "summary": "Valid entry",
                "detail": "Good",
                "impact": 0.8,
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 1
        assert result["total_source"] == 4
