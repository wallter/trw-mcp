"""Shared helpers for memory store test splits."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from trw_mcp.state.memory_store import MemoryStore

_sqlite_vec = pytest.importorskip("sqlite_vec", reason="sqlite-vec not installed (optional [vectors] extra)")


def _make_store(tmp_path: Path, dim: int = 4) -> MemoryStore:
    from trw_mcp.state.memory_store import MemoryStore as _MemoryStore

    return _MemoryStore(tmp_path / "vectors.db", dim=dim)
