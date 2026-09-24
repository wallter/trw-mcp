"""Split bootstrap IDE detection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import detect_ide, detect_installed_clis, resolve_ide_targets


class TestIDEDetection:
    """Tests for detect_ide, detect_installed_clis, and resolve_ide_targets."""

    @pytest.fixture(autouse=True)
    def _isolate_path_binaries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clear cursor/cursor-agent from PATH lookup so detect_ide tests are
        deterministic regardless of the developer's installed IDEs.

        Sprint 91 added shutil.which("cursor") + shutil.which("cursor-agent")
        to detect_ide so a globally-installed Cursor IDE/CLI doesn't leak
        into these unit tests' tmp_path fixtures. Also blocks CURSOR_API_KEY
        / CURSOR_TRACE_ID env-var leakage.
        """
        import shutil as _shutil

        from trw_mcp.bootstrap import _utils

        original_which = _shutil.which

        def _which_filtered(cmd: str, *args: object, **kwargs: object) -> str | None:
            if cmd in {"cursor", "cursor-agent"}:
                return None
            return original_which(cmd, *args, **kwargs)

        monkeypatch.setattr(_utils.shutil, "which", _which_filtered)
        monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    def test_fr08_detect_claude_code(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        result = detect_ide(tmp_path)
        assert result == ["claude-code"]

    def test_fr08_detect_cursor(self, tmp_path: Path) -> None:
        """detect_ide(.cursor/ dir) returns cursor-ide (renamed from cursor in Sprint 91)."""
        # The .cursor dir presence alone triggers cursor-ide detection
        (tmp_path / ".cursor").mkdir()
        result = detect_ide(tmp_path)
        assert "cursor-ide" in result

    def test_fr08_detect_opencode_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".opencode").mkdir()
        result = detect_ide(tmp_path)
        assert result == ["opencode"]

    def test_fr08_detect_opencode_json(self, tmp_path: Path) -> None:
        (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")
        result = detect_ide(tmp_path)
        assert result == ["opencode"]

    def test_fr08_detect_codex_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        result = detect_ide(tmp_path)
        assert result == ["codex"]

    def test_fr08_detect_codex_config(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text("", encoding="utf-8")
        result = detect_ide(tmp_path)
        assert result == ["codex"]

    def test_fr08_detect_multiple(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".opencode").mkdir()
        result = detect_ide(tmp_path)
        assert "claude-code" in result
        assert "opencode" in result

    def test_fr08_detect_none(self, tmp_path: Path) -> None:
        result = detect_ide(tmp_path)
        assert result == []

    def test_fr08_resolve_override(self, tmp_path: Path) -> None:
        result = resolve_ide_targets(tmp_path, ide_override="opencode")
        assert result == ["opencode"]

    def test_fr08_resolve_all(self, tmp_path: Path) -> None:
        result = resolve_ide_targets(tmp_path, ide_override="all")
        assert "claude-code" in result
        assert "opencode" in result
        assert "cursor-ide" in result
        assert "codex" in result

    def test_fr08_resolve_default_claude(self, tmp_path: Path) -> None:
        # No IDE detected → default to claude-code
        result = resolve_ide_targets(tmp_path)
        assert result == ["claude-code"]

    def test_fr08_resolve_auto_detect(self, tmp_path: Path) -> None:
        (tmp_path / ".opencode").mkdir()
        result = resolve_ide_targets(tmp_path)
        assert result == ["opencode"]

    def test_fr08_resolve_auto_detect_codex(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        result = resolve_ide_targets(tmp_path)
        assert result == ["codex"]

    def test_fr08_detect_installed_clis_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_installed_clis returns only CLIs found on PATH."""
        import shutil as _shutil

        def fake_which(cmd: str) -> str | None:
            return {
                "claude": "/usr/bin/claude",
                "codex": "/usr/bin/codex",
            }.get(cmd)

        monkeypatch.setattr(_shutil, "which", fake_which)
        # Also patch the shutil reference inside the bootstrap module
        monkeypatch.setattr("trw_mcp.bootstrap._utils.shutil.which", fake_which)
        result = detect_installed_clis()
        assert result == ["claude-code", "codex"]

    def test_fr08_detect_installed_clis_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_installed_clis returns empty list when no CLIs found."""
        monkeypatch.setattr("trw_mcp.bootstrap._utils.shutil.which", lambda _cmd: None)
        result = detect_installed_clis()
        assert result == []
