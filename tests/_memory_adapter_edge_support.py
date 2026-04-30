from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d
