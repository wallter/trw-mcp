"""YAML-path resolution tests for state/memory_adapter.py.

PRD-CORE-280 slice e1: ``TestEnsureMigrated`` (YAML-to-SQLite migration) and
``TestGetBackend`` (singleton identity, auto-migration, corruption-retry
construction) were deleted, not ported — every case constructed the SQLite
backend directly and/or called its singleton accessor, both names the next
slice deletes outright. ``TestFindYamlPathForEntry`` touches only the
filesystem (no store object), so it is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import get_config
from trw_mcp.state.memory_adapter import find_yaml_path_for_entry

from ._memory_adapter_support import trw_dir  # noqa: F401


class TestFindYamlPathForEntry:
    """PRD-FIX-033-FR05: find_yaml_path_for_entry resolves YAML paths."""

    def test_finds_existing_yaml(self, trw_dir: Path) -> None:
        """Finds YAML file when it exists with sanitized name."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "L-test1.yaml"
        writer.write_yaml(yaml_path, {"id": "L-test1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-test1")
        assert result is not None
        assert result.name == "L-test1.yaml"

    def test_missing_entry_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entry does not exist."""
        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        result = find_yaml_path_for_entry(trw_dir, "L-nonexistent")
        assert result is None

    def test_no_entries_dir_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entries directory does not exist."""
        result = find_yaml_path_for_entry(trw_dir, "L-any")
        assert result is None

    def test_date_prefixed_filename(self, trw_dir: Path) -> None:
        """Finds YAML file with date-prefixed filename containing entry ID."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "2026-02-01-L-dated1-summary-words.yaml"
        writer.write_yaml(yaml_path, {"id": "L-dated1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-dated1")
        assert result is not None
        assert "L-dated1" in result.stem
