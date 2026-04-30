"""Shared helpers for split auto-recall tests."""

from __future__ import annotations

from pathlib import Path


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create minimal .trw/ directory structure for tests."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir
