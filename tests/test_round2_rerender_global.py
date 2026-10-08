"""G1 regressions: shared configs, rerender ownership/mode, and global rollback."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from tests.test_update_project_c1_controls import project
from trw_mcp.bootstrap import _update_project as updater
from trw_mcp.bootstrap import update_project
from trw_mcp.bootstrap._antigravity_cli import (
    _antigravity_global_mcp_config_path,
    generate_antigravity_mcp_config,
)
from trw_mcp.bootstrap._rerender import _requested_paths
from trw_mcp.bootstrap._version_manifest import _is_user_modified
from trw_mcp.bootstrap._written_digests import load_written_digests

pytestmark = pytest.mark.usefixtures("no_memory_daemon")
HOOK = ".claude/hooks/session-start.sh"


@pytest.mark.parametrize(
    "rel",
    [
        ".mcp.json",
        ".claude/settings.json",
        "opencode.json",
        ".codex/config.toml",
        ".antigravitycli/settings.json",
        "AGENTS.md",
        ".gitignore",
    ],
)
def test_rerender_refuses_shared_surfaces(tmp_path: Path, rel: str) -> None:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"user content")
    with pytest.raises(ValueError, match=r"plain.*update-project"):
        _requested_paths(tmp_path, [rel])
    assert path.read_bytes() == b"user content"


def test_rerender_records_new_baselines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path, "claude-code")
    path = root / HOOK
    path.write_bytes(b"hand edited hook")
    current = b"#!/bin/sh\necho current release\n"

    def render(scratch: Path, *args: object, **kwargs: object) -> None:
        (scratch / HOOK).write_bytes(current)
        (scratch / HOOK).chmod(0o755)

    monkeypatch.setattr(updater, "_apply_update", render)
    before = YAML(typ="safe").load(root / ".trw/managed-artifacts.yaml")
    result = update_project(root, rerender=[HOOK])
    assert not result["errors"], result
    after = YAML(typ="safe").load(root / ".trw/managed-artifacts.yaml")
    digest = hashlib.sha256(current).hexdigest()
    assert after["content_hashes"][path.name] == digest
    assert load_written_digests(root)[HOOK] == digest
    assert not _is_user_modified(path, path.name, after["content_hashes"], framework_hashes={"later-release"})
    for key, value in before["content_hashes"].items():
        if key != path.name:
            assert after["content_hashes"][key] == value


@pytest.mark.parametrize("edited", [False, True])
def test_rerender_repairs_hook_mode(tmp_path: Path, edited: bool) -> None:
    root = project(tmp_path, "claude-code")
    path = root / HOOK
    expected = path.read_bytes()
    expected_mode = stat.S_IMODE(path.stat().st_mode)
    assert expected_mode & 0o111
    if edited:
        path.write_bytes(b"user changed hook")
    path.chmod(0o644)
    preview = update_project(root, rerender=[HOOK], dry_run=True)
    assert not preview["errors"], preview
    assert HOOK in preview["updated"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    result = update_project(root, rerender=[HOOK])
    assert not result["errors"], result
    assert path.read_bytes() == expected
    assert stat.S_IMODE(path.stat().st_mode) == expected_mode
    assert HOOK in result["updated"]


@pytest.mark.parametrize("link_part", [".gemini", ".gemini/config", ".gemini/config/mcp_config.json"])
def test_global_config_follows_dotfile_links(tmp_path: Path, link_part: str) -> None:
    link = Path.home() / link_part
    target = tmp_path / "dotfiles" / "target"
    link.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True)
    if link_part.endswith(".json"):
        target.write_bytes(b"{}")
        link.symlink_to(target)
    else:
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
    path = _antigravity_global_mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    old = b'{"theme":"dark","mcpServers":{"foreign":{"command":"keep"}}}\n'
    path.write_bytes(old)
    resolved = path.resolve()
    result = generate_antigravity_mcp_config(tmp_path)
    assert not result["errors"], result
    assert link.is_symlink()
    data = json.loads(resolved.read_bytes())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["foreign"] == {"command": "keep"}
    assert "trw" in data["mcpServers"]
    backups = [p for p in tmp_path.rglob("data") if p.read_bytes() == old]
    assert backups
    assert any(str(p) in warning for p in backups for warning in result["warnings"])


@pytest.mark.parametrize("existing", [False, True])
def test_failed_update_restores_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool) -> None:
    root = project(tmp_path)
    path = _antigravity_global_mcp_config_path()
    old = b'{"theme":"dark","mcpServers":{"foreign":{"command":"keep"}}}\n'
    if existing:
        path.parent.mkdir(parents=True)
        path.write_bytes(old)
    observed: list[bytes] = []

    def fail_verify(*args: object, **kwargs: object) -> None:
        observed.append(path.read_bytes())
        assert "trw" in json.loads(observed[-1])["mcpServers"]
        raise OSError("injected verification failure")

    monkeypatch.setattr(updater, "_verify_installation", fail_verify)
    result = update_project(root, ide="antigravity-cli")
    assert observed, result
    assert result["errors"]
    if existing:
        assert path.read_bytes() == old
    else:
        assert not path.exists()
    assert any("GLOBAL" in warning and "restored" in warning for warning in result["warnings"])


@pytest.mark.parametrize("atomic", [False, True])
def test_global_rollback_keeps_concurrent_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, atomic: bool) -> None:
    root = project(tmp_path)
    path = _antigravity_global_mcp_config_path()
    path.parent.mkdir(parents=True)
    old = b'{"theme":"original"}\n'
    path.write_bytes(old)
    concurrent = b'{"theme":"concurrent"}\n'
    raced: list[bool] = []

    def fail_verify(*args: object, **kwargs: object) -> None:
        assert "trw" in json.loads(path.read_bytes())["mcpServers"]
        raced.append(True)
        if atomic:
            incoming = path.with_suffix(".incoming")
            incoming.write_bytes(concurrent)
            incoming.replace(path)
        else:
            path.write_bytes(concurrent)
        raise OSError("injected after concurrent save")

    monkeypatch.setattr(updater, "_verify_installation", fail_verify)
    result = update_project(root, ide="antigravity-cli")
    assert raced
    assert path.read_bytes() == concurrent
    assert any("GLOBAL" in error and "rollback refused" in error for error in result["errors"])
    backups = [p for p in (Path.home() / ".trw/trash").rglob("data") if p.read_bytes() == old]
    assert backups
    assert any(str(p) in error for p in backups for error in result["errors"])


def test_global_symlink_survives_interrupted_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    link = _antigravity_global_mcp_config_path()
    link.parent.mkdir(parents=True)
    target = tmp_path / "dotfile.json"
    old = b'{"theme":"original"}\n'
    target.write_bytes(old)
    target.chmod(0o600)
    link.symlink_to(target)
    observed: list[bytes] = []

    def interrupt(*args: object, **kwargs: object) -> None:
        observed.append(target.read_bytes())
        assert "trw" in json.loads(observed[-1])["mcpServers"]
        raise KeyboardInterrupt

    monkeypatch.setattr(updater, "_verify_installation", interrupt)
    with pytest.raises(KeyboardInterrupt):
        update_project(root, ide="antigravity-cli")
    assert observed
    assert link.is_symlink()
    assert target.read_bytes() == old
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_global_symlink_initializes_missing_config(tmp_path: Path) -> None:
    target = tmp_path / "dotfiles"
    target.mkdir()
    link = Path.home() / ".gemini"
    link.symlink_to(target, target_is_directory=True)
    result = generate_antigravity_mcp_config(tmp_path)
    assert not result["errors"], result
    assert link.is_symlink()
    assert "trw" in json.loads((target / "config/mcp_config.json").read_bytes())["mcpServers"]
