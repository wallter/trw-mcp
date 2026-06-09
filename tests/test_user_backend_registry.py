"""PRD-CORE-185 FR02: lazy user-space backend singleton.

``get_user_backend()`` lazily creates a SECOND ``SQLiteBackend`` singleton
rooted at ``resolve_user_memory_dir()``, distinct from the project backend and
idempotent across calls (shared box-wide).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state import _memory_connection
from trw_mcp.state._user_tier import get_user_backend, peek_user_backend, reset_user_backend


@pytest.fixture(autouse=True)
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the user store at a clean tmp dir and reset the singleton."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    reset_user_backend()
    yield
    reset_user_backend()


def test_returns_backend_at_user_dir(tmp_path: Path) -> None:
    """The user backend is a SQLiteBackend rooted under the user memory dir."""
    backend = get_user_backend()
    assert isinstance(backend, SQLiteBackend)
    # DB file lives under the resolved user memory dir.
    expected_db = (tmp_path / "userhome" / "memory" / "memory.db").resolve()
    assert expected_db.exists()


def test_singleton_idempotent() -> None:
    """Repeated calls return the SAME instance (box-wide singleton)."""
    first = get_user_backend()
    second = get_user_backend()
    assert first is second


def test_distinct_from_project_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """User and project backends are distinct instances on distinct DB files."""
    # Point the PROJECT backend at its own tmp .trw dir.
    project_trw = tmp_path / "proj" / ".trw"
    project_trw.mkdir(parents=True)
    _memory_connection.reset_backend()
    try:
        project_backend = _memory_connection.get_backend(trw_dir=project_trw)
        user_backend = get_user_backend()
        assert project_backend is not user_backend
        assert project_backend.db_path != user_backend.db_path
    finally:
        _memory_connection.reset_backend()


def test_lazy_peek_returns_none_before_construction() -> None:
    """``peek_user_backend`` does not construct a backend."""
    reset_user_backend()
    assert peek_user_backend() is None
    get_user_backend()
    assert peek_user_backend() is not None
