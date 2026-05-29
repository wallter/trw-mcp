from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.storage.sqlite_backend import SQLiteBackend


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


def _make_backend(trw_dir: Path) -> SQLiteBackend:
    """Create a fresh SQLiteBackend for a test trw_dir."""
    db_path = trw_dir / "memory" / "memory.db"
    return SQLiteBackend(db_path)
