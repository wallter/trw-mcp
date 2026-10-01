"""The doctor's `memory_wal` row sees a checkpoint the STORE performed (UF-MEM-26).

The row reads marker files beside the user store. Before, only trw-mcp helpers nothing calls wrote them, and the tests
stamped them by hand. This drives the production writer (a real backend checkpoint) and reads it back through the row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig


def test_a_store_checkpoint_makes_the_wal_row_report_ages_not_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend
    from trw_memory.user_paths import resolve_user_memory_dir

    from trw_mcp.server._doctor_memory_wal import memory_wal_row

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "user"))
    db = resolve_user_memory_dir(create=True) / "memory.db"
    backend = SQLiteBackend(db)
    backend.store(MemoryEntry(id="M-wal", content="a row so the WAL has frames", namespace="default"))
    row_before = memory_wal_row(tmp_path, TRWConfig())[1]
    backend.checkpoint_wal()
    backend.close()
    row_after = memory_wal_row(tmp_path, TRWConfig())[1]

    assert "last checkpoint attempt unknown ago" in row_before
    assert "last checkpoint attempt unknown ago" not in row_after
    assert "last checkpoint attempt " in row_after and "s ago" in row_after
