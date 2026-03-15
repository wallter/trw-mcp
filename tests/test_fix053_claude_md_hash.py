"""Tests for PRD-FIX-053-FR04: Content-hash change detection for claude_md_sync.

Verifies that consecutive claude_md_sync calls with no learning changes
return status='unchanged' and skip the full render.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_sync_args(tmp_path: Path) -> dict:
    """Build minimal args for execute_claude_md_sync using tmp_path as root."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    (trw_dir / "patterns").mkdir(exist_ok=True)

    config = TRWConfig(trw_dir=str(trw_dir))
    reader = FileStateReader()
    writer = FileStateWriter()
    llm = MagicMock()
    llm.available = False

    return {
        "scope": "root",
        "target_dir": None,
        "config": config,
        "reader": reader,
        "writer": writer,
        "llm": llm,
    }


class TestClaudeMdHashDetection:
    """FR04: Content-hash change detection skips redundant renders."""

    def test_second_call_returns_unchanged(self, tmp_path: Path) -> None:
        """Two consecutive syncs with same learnings → second returns status='unchanged'."""
        import trw_mcp.state.claude_md as _pkg
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = _make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch.object(_pkg, "resolve_trw_dir", return_value=trw_dir),
            patch.object(_pkg, "resolve_project_root", return_value=tmp_path),
        ):
            # First call — renders and writes hash
            result1 = execute_claude_md_sync(**args)
            assert result1["status"] in ("synced", "unchanged")

            # Second call — inputs unchanged → should return unchanged
            result2 = execute_claude_md_sync(**args)
            assert result2["status"] == "unchanged"

    def test_hash_file_created_after_first_sync(self, tmp_path: Path) -> None:
        """First sync writes claude_md_hash.txt to .trw/context/."""
        import trw_mcp.state.claude_md as _pkg
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = _make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch.object(_pkg, "resolve_trw_dir", return_value=trw_dir),
            patch.object(_pkg, "resolve_project_root", return_value=tmp_path),
        ):
            execute_claude_md_sync(**args)

        hash_file = trw_dir / "context" / "claude_md_hash.txt"
        assert hash_file.exists(), "Hash file must be written after first sync"
        content = hash_file.read_text(encoding="utf-8").strip()
        assert len(content) == 64, "SHA-256 hex digest should be 64 chars"

    def test_new_learning_invalidates_cache(self, tmp_path: Path) -> None:
        """Adding a learning causes second sync to re-render (status='synced')."""
        import trw_mcp.state.claude_md as _pkg
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync
        from trw_mcp.state.memory_adapter import store_learning

        args = _make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch.object(_pkg, "resolve_trw_dir", return_value=trw_dir),
            patch.object(_pkg, "resolve_project_root", return_value=tmp_path),
        ):
            # First sync establishes hash
            result1 = execute_claude_md_sync(**args)
            assert result1["status"] in ("synced", "unchanged")

            # Add a learning — should invalidate the hash
            store_learning(
                trw_dir,
                "L-hash001",
                "New learning summary",
                "New detail",
                tags=["test"],
                impact=0.8,
            )

            # Second sync should re-render
            result2 = execute_claude_md_sync(**args)
            assert result2["status"] == "synced", "After adding a learning, sync must re-render (not return unchanged)"

    def test_unchanged_returns_hash_field(self, tmp_path: Path) -> None:
        """When status=unchanged, response includes hash field."""
        import trw_mcp.state.claude_md as _pkg
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = _make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch.object(_pkg, "resolve_trw_dir", return_value=trw_dir),
            patch.object(_pkg, "resolve_project_root", return_value=tmp_path),
        ):
            execute_claude_md_sync(**args)
            result2 = execute_claude_md_sync(**args)

        assert result2["status"] == "unchanged"
        assert "hash" in result2, "unchanged response must include hash field"

    def test_missing_hash_file_forces_render(self, tmp_path: Path) -> None:
        """If hash file is deleted, next sync re-renders."""
        import trw_mcp.state.claude_md as _pkg
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = _make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch.object(_pkg, "resolve_trw_dir", return_value=trw_dir),
            patch.object(_pkg, "resolve_project_root", return_value=tmp_path),
        ):
            # First sync
            execute_claude_md_sync(**args)

            # Delete hash file
            hash_file = trw_dir / "context" / "claude_md_hash.txt"
            if hash_file.exists():
                hash_file.unlink()

            # Next sync should re-render
            result = execute_claude_md_sync(**args)
            assert result["status"] == "synced"
