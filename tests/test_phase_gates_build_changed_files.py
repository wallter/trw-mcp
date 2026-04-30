from __future__ import annotations

from pathlib import Path

import pytest


class TestGetChangedFiles:
    """Tests for _get_changed_files via subprocess mock."""

    def test_returns_deduped_file_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess as subprocess_mod

        from trw_mcp.state.validation import phase_gates_build as pgb

        call_count = 0

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess_mod.CompletedProcess:  # type: ignore[type-arg]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return subprocess_mod.CompletedProcess(cmd, 0, stdout="foo.py\nbar.py\n")
            if call_count == 2:
                return subprocess_mod.CompletedProcess(cmd, 0, stdout="bar.py\nbaz.py\n")
            return subprocess_mod.CompletedProcess(cmd, 0, stdout="qux.py\n")

        monkeypatch.setattr(subprocess_mod, "run", fake_run)
        result = pgb._get_changed_files(tmp_path)
        assert "foo.py" in result
        assert "bar.py" in result
        assert "baz.py" in result
        assert "qux.py" in result
        assert len(result) == len(set(result))

    def test_returns_empty_on_subprocess_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.SubprocessError("fail")),
        )
        result = pgb._get_changed_files(tmp_path)
        assert result == []

    def test_returns_empty_on_file_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("git not found")),
        )
        result = pgb._get_changed_files(tmp_path)
        assert result == []
