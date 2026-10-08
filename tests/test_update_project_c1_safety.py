"""Selective rerender refusal, preview, backup, and concurrency contracts."""

from pathlib import Path

import pytest

from tests.test_update_project_c1_controls import project
from trw_mcp.bootstrap import update_project
from trw_mcp.bootstrap._antigravity_cli import _antigravity_global_mcp_config_path, generate_antigravity_mcp_config
from trw_mcp.bootstrap._rerender import publish_with_backup, read_optional
from trw_mcp.bootstrap._utils import _verify_installation

pytestmark = pytest.mark.usefixtures("no_memory_daemon")
AGENT = ".codex/agents/trw-implementer.toml"


@pytest.mark.parametrize(
    "rel",
    ["../outside", "/tmp/outside", ".trw/config.yaml", ".git/config", ".codex/agents", ".codex/agents/custom.toml"],
)
def test_rerender_refuses_invalid_or_unrenderable_paths(tmp_path: Path, rel: str) -> None:
    root = project(tmp_path)
    before = (root / AGENT).read_bytes()
    result = update_project(root, rerender=[AGENT, rel])
    assert result["errors"]
    assert (root / AGENT).read_bytes() == before
    assert not (root / ".trw/trash").exists()


def test_rerender_preview_repeatable_and_legacy_warning(tmp_path: Path) -> None:
    root = project(tmp_path)
    other = ".codex/agents/trw-auditor.toml"
    expected = {rel: (root / rel).read_bytes() for rel in (AGENT, other)}
    for rel in expected:
        (root / rel).write_text('name = "legacy"\nmodel = "gpt-5.4"\n')
    checked: dict[str, list[str]] = {"warnings": [], "errors": []}
    _verify_installation(root, checked, expects_mcp_json=False)
    assert any(f"trw-mcp update-project --rerender {AGENT}" in note for note in checked["warnings"])
    preview = update_project(root, rerender=list(expected), dry_run=True)
    assert not preview["errors"], preview["errors"]
    assert set(preview["updated"]) == set(expected)
    assert not (root / ".trw/trash").exists()
    assert b"gpt-5.4" in (root / AGENT).read_bytes()
    # Removing the pin still leaves an edited file; explicit rerender overrides that guard too.
    (root / AGENT).write_text('name = "legacy"\n')
    result = update_project(root, rerender=[AGENT, other, AGENT])
    assert not result["errors"], result["errors"]
    assert len(result["updated"]) == len(expected)
    for rel, data in expected.items():
        assert (root / rel).read_bytes() == data


def test_rerender_tombstoned_file_is_explicitly_recreated(tmp_path: Path) -> None:
    root = project(tmp_path)
    expected = (root / AGENT).read_bytes()
    (root / AGENT).unlink()
    result = update_project(root)
    assert not result["errors"], result["errors"]
    assert not (root / AGENT).exists()
    result = update_project(root, rerender=[AGENT])
    assert not result["errors"], result["errors"]
    assert (root / AGENT).read_bytes() == expected
    result = update_project(root)
    assert not result["errors"], result["errors"]
    assert (root / AGENT).read_bytes() == expected


def test_rerender_rejects_leaf_and_parent_symlinks(tmp_path: Path) -> None:
    root = project(tmp_path)
    external = tmp_path / "external"
    external.write_bytes(b"keep outside")
    (root / AGENT).unlink()
    (root / AGENT).symlink_to(external)
    result = update_project(root, rerender=[AGENT])
    assert result["errors"]
    assert external.read_bytes() == b"keep outside"
    assert (root / AGENT).is_symlink()


@pytest.mark.parametrize("atomic", [False, True])
def test_publish_keeps_concurrent_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, atomic: bool) -> None:
    from trw_mcp.bootstrap import _trash

    root = tmp_path
    path = root / "managed.txt"
    path.write_bytes(b"old")
    remove = _trash.remove_if_hash
    raced = []

    def race(*args: object, **kwargs: object) -> object:
        raced.append(True)
        if atomic:
            incoming = root / "incoming"
            incoming.write_bytes(b"concurrent edit")
            incoming.replace(path)
        else:
            path.write_bytes(b"concurrent edit")
        return remove(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_trash, "remove_if_hash", race)
    result: dict[str, list[str]] = {}
    assert not publish_with_backup(root, path.name, b"old", b"rendered", result)
    assert raced
    assert result["errors"]
    assert path.read_bytes() == b"concurrent edit"


def test_backup_failure_blocks_global_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _trash

    path = _antigravity_global_mcp_config_path()
    path.parent.mkdir(parents=True)
    old = b'{"mcpServers":{"mine":{"command":"mine"}}}'
    path.write_bytes(old)

    def fail(*args: object, **kwargs: object) -> None:
        raise PermissionError("backup unavailable")

    monkeypatch.setattr(_trash, "remove_if_hash", fail)
    result = generate_antigravity_mcp_config(tmp_path)
    assert result["errors"]
    assert path.read_bytes() == old


def test_unreadable_is_not_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _rerender

    def fail(*args: object, **kwargs: object) -> None:
        raise PermissionError("read refused")

    monkeypatch.setattr(_rerender, "open_under", fail)
    with pytest.raises(PermissionError):
        read_optional(tmp_path, "managed.txt")
    result = generate_antigravity_mcp_config(tmp_path)
    assert result["errors"]
    assert not _antigravity_global_mcp_config_path().exists()


def test_resolved_clients_are_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._subcommands import _summarize_update_result

    result: dict[str, list[str]] = {k: [] for k in ("updated", "created", "cleaned", "preserved", "errors", "warnings")}
    result["clients"] = ["codex", "grok"]
    _summarize_update_result(result, target=tmp_path, dry_run=True, ide="all")
    assert "Resolved clients: codex, grok" in capsys.readouterr().out


def test_rerender_deleted_hook_keeps_rendered_executable_mode(tmp_path: Path) -> None:
    root = project(tmp_path, "claude-code")
    hook = root / ".claude/hooks/session-end.sh"
    expected = hook.read_bytes()
    assert hook.stat().st_mode & 0o111
    hook.unlink()
    result = update_project(root, rerender=[hook.relative_to(root).as_posix()])
    assert not result["errors"], result["errors"]
    assert hook.read_bytes() == expected
    assert hook.stat().st_mode & 0o111
