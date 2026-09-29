"""The adoption probe runs client writers; it must write nothing outside its scratch copy.

Antigravity's MCP config is GLOBAL (``~/.gemini/config/mcp_config.json``). The probe used to run that
writer against the real HOME while only *previewing* what it would overwrite in the project.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap._client_adoption import WritersNotEnumerable, _probe_home, writer_overwrites

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _snapshot(home: Path) -> dict[str, bytes]:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".gemini" / "config").mkdir(parents=True)
    (home / ".gemini" / "config" / "mcp_config.json").write_text('{"mcpServers": {"mine": {"command": "x"}}}\n')
    (home / ".Trash").mkdir()
    (home / "notes.txt").write_text("user file\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text("target_platforms: [codex]\n")
    return root


@pytest.mark.parametrize("client", ["antigravity-cli", "codex", "opencode", "copilot", "cursor-ide", "grok"])
def test_writer_probe_leaves_home_byte_identical(home: Path, project: Path, client: str) -> None:
    before = _snapshot(home)
    dirs_before = sorted(p.relative_to(home) for p in home.rglob("*") if p.is_dir())

    with contextlib.suppress(WritersNotEnumerable):  # failing closed is fine; only the side effect is under test
        writer_overwrites(project, client, {})

    assert _snapshot(home) == before
    assert sorted(p.relative_to(home) for p in home.rglob("*") if p.is_dir()) == dirs_before


def test_probe_home_redirects_every_global_root_and_restores_the_environment(home: Path) -> None:
    before = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}

    with _probe_home() as fake:
        assert Path.home() == fake != home
        assert Path(os.environ["XDG_CONFIG_HOME"]).is_relative_to(fake)
        assert Path(os.environ["XDG_DATA_HOME"]).is_relative_to(fake)
        (Path.home() / "x").write_text("y")

    assert not fake.exists()
    assert {k: os.environ.get(k) for k in before} == before
