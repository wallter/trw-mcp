"""E2E-INC-121 tip 2 (swarm-e2e S13-A4): help and usage that say what the command actually does.

(h) ``init-project claude-code`` silently created ``./claude-code/`` -- the client id belongs in ``--ide``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_init_project_with_a_client_id_as_the_path_asks_about_ide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """INC-121 (h): `init-project claude-code` silently created ./claude-code/; the client belongs in --ide."""
    from trw_mcp.server._cli_argparse import _build_arg_parser

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as done:
        _build_arg_parser().parse_args(["init-project", "claude-code"])
    assert done.value.code == 2
    assert "--ide claude-code" in capsys.readouterr().err and not (tmp_path / "claude-code").exists()


def test_init_project_accepts_an_existing_directory_named_like_a_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    monkeypatch.chdir(tmp_path)
    (tmp_path / "codex").mkdir()
    assert _build_arg_parser().parse_args(["init-project", "codex"]).target_dir == "codex"
