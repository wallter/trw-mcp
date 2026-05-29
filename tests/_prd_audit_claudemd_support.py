"""Shared support for split PRD, audit, and Claude MD coverage tests."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_writer = FileStateWriter()
_reader = FileStateReader()


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal .trw project structure."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    return tmp_path
