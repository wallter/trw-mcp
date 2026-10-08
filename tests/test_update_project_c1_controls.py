"""Regression contracts for the rerender and client-selection feedback."""

import argparse
import json
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._antigravity_cli import (
    _antigravity_global_mcp_config_path,
    generate_antigravity_mcp_config,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES, resolve_client_write_targets

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def project(tmp_path: Path, client: str = "codex") -> Path:
    root = tmp_path / "project"
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    result = init_project(root, ide=client)
    assert not result["errors"], result["errors"]
    return root


def commit(root: Path, rel: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "--", rel], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/agents/trw-distill-explorer.md",
        ".claude/skills/trw-audit/audit-framework.md",
    ],
)
def test_deleted_tracked_non_tombstoned_file_renders(tmp_path: Path, rel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **kwargs: True)
    root = project(tmp_path, "claude-code")
    path = root / rel
    assert path.is_file()
    commit(root, rel)
    # The report concerns files without a manifest baseline (not tombstones).
    manifest = root / ".trw/managed-artifacts.yaml"
    yaml = YAML()
    data = yaml.load(manifest)
    from trw_mcp.bootstrap._version_manifest import _manifest_key_path

    data["content_hashes"] = {k: v for k, v in data["content_hashes"].items() if _manifest_key_path(k) != rel}
    with manifest.open("w") as stream:
        yaml.dump(data, stream)
    path.unlink()
    result = update_project(root)
    assert not result["errors"], result["errors"]
    assert path.is_file(), result
    assert f"{rel} (uncommitted_changes)" not in result["preserved"]


def test_all_means_recorded_but_explicit_client_can_be_added(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert resolve_client_write_targets(root, "all") == ["codex"]
    result = update_project(root, ide="all")
    assert not result["errors"], result["errors"]
    assert result["clients"] == ["codex"]
    assert not (root / "ANTIGRAVITY.md").exists()
    assert not _antigravity_global_mcp_config_path().exists()
    assert resolve_client_write_targets(root, "grok") == ["grok"]
    assert set(resolve_client_write_targets(tmp_path / "empty", "all")) == set(SUPPORTED_IDES)


def test_rerender_only_selected_paths_with_backup(tmp_path: Path) -> None:
    root = project(tmp_path)
    rel = ".codex/agents/trw-implementer.toml"
    other = root / ".codex/agents/trw-auditor.toml"
    expected = (root / rel).read_bytes()
    commit(root, rel)
    old = b'name = "trw_implementer"\nmodel = "gpt-5.4"\n'
    (root / rel).write_bytes(old)
    other.write_bytes(b"user edit stays\n")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
    result = update_project(root, rerender=[rel])
    assert not result["errors"], result["errors"]
    assert (root / rel).read_bytes() == expected
    assert result["updated"] == [rel]
    for name, content in before.items():
        if name.as_posix() not in {rel, ".trw/managed-artifacts.yaml", ".trw/runtime/written-digests.json"}:
            assert (root / name).read_bytes() == content, name
    backups = [p for p in (root / ".trw/trash").rglob("data") if p.read_bytes() == old]
    assert backups
    assert any(str(backups[0]) in note for note in result["warnings"])


def test_rerender_parser_and_cli_forwarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._cli_argparse_project import add_project_subcommands
    from trw_mcp.server._subcommands import _run_update_project

    parser = argparse.ArgumentParser()
    add_project_subcommands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "update-project",
            str(tmp_path),
            "--rerender",
            ".codex/agents/trw-implementer.toml",
            "--rerender",
            ".codex/INSTRUCTIONS.md",
        ]
    )
    captured: dict[str, object] = {}

    def fake_update(root: Path, **kwargs: object) -> dict[str, list[str]]:
        captured.update(kwargs)
        return {k: [] for k in ("errors", "warnings", "created", "updated", "preserved", "cleaned")}

    monkeypatch.setattr("trw_mcp.bootstrap.update_project", fake_update)
    with pytest.raises(SystemExit) as exc:
        _run_update_project(args)
    assert exc.value.code == 0
    assert captured["rerender"] == args.rerender


def test_global_config_backup_survives_success(tmp_path: Path) -> None:
    path = _antigravity_global_mcp_config_path()
    path.parent.mkdir(parents=True)
    old = b'{"mcpServers":{"foreign":{"command":"keep"}}}\n'
    path.write_bytes(old)
    result = generate_antigravity_mcp_config(tmp_path)
    assert not result["errors"], result["errors"]
    assert json.loads(path.read_bytes())["mcpServers"]["foreign"] == {"command": "keep"}
    backups = [p for p in (Path.home() / ".trw/trash").rglob("data") if p.read_bytes() == old]
    assert backups
    assert any(str(backups[0]) in note for note in result["warnings"])


def test_antigravity_dry_run_names_global_write_without_touching_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    path = _antigravity_global_mcp_config_path()
    path.parent.mkdir(parents=True)
    old = b'{"mcpServers":{"foreign":{"command":"keep"}}}\n'
    path.write_bytes(old)
    result = update_project(root, ide="antigravity-cli", dry_run=True)
    assert not result["errors"], result["errors"]
    assert path.read_bytes() == old
    assert not (Path.home() / ".trw/trash").exists()
    assert any("~/.gemini/config/mcp_config.json" in note for note in result["warnings"])
