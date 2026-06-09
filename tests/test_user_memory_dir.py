"""PRD-CORE-185 FR01: machine-local user-space memory dir resolver.

``resolve_user_memory_dir()`` returns the machine-local ``trw/memory``
directory with precedence: ``TRW_USER_DIR`` > ``$XDG_DATA_HOME`` > ``~/.trw``.
Cross-platform, creates parents lazily, never raises on absence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._user_paths import resolve_user_memory_dir


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no ambient TRW_USER_DIR / XDG_DATA_HOME leaks into a test."""
    monkeypatch.delenv("TRW_USER_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


def test_trw_user_dir_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TRW_USER_DIR`` overrides everything → ``<TRW_USER_DIR>/memory``."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "u"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = resolve_user_memory_dir()
    assert result == (tmp_path / "u" / "memory").resolve()


def test_xdg_data_home_used_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With XDG set and no TRW_USER_DIR → ``$XDG_DATA_HOME/trw/memory``."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = resolve_user_memory_dir()
    assert result == (tmp_path / "xdg" / "trw" / "memory").resolve()


def test_home_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With neither set → ``<home>/.trw/memory``."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    result = resolve_user_memory_dir()
    assert result == (tmp_path / "home" / ".trw" / "memory").resolve()


def test_creates_parent_lazily(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The resolver creates the directory and never raises on absence."""
    target = tmp_path / "fresh"
    monkeypatch.setenv("TRW_USER_DIR", str(target))
    assert not target.exists()
    result = resolve_user_memory_dir()
    assert result.exists()
    assert result.is_dir()
