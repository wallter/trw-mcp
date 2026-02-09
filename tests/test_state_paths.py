"""Tests for shared path resolution in trw_mcp.state._paths."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestResolveProjectRoot:
    """Tests for resolve_project_root()."""

    def test_uses_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns TRW_PROJECT_ROOT env var when set."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == tmp_path.resolve()

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns CWD when env var is not set."""
        monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == Path.cwd().resolve()

    def test_resolves_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result.is_absolute()


class TestResolveTrwDir:
    """Tests for resolve_trw_dir()."""

    def test_returns_trw_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns project_root / .trw."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result == tmp_path.resolve() / ".trw"

    def test_is_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result.is_absolute()
