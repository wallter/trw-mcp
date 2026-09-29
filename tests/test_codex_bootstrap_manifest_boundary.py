"""Bootstrap eligibility comes from the manifest, never session middleware.

PRD-CORE-301 FR07 adds the file boundary: ``init-project --ide codex`` writes
only the hook scripts ``.codex/hooks.json`` runs (plus the helpers those
scripts source), and ``managed-artifacts.yaml`` records every one of them.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tomllib

from trw_mcp.bootstrap import _codex as codex
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._version_manifest import _manifest_key_path, _read_manifest
from trw_mcp.server import _surface_manifest_registry as registry
from trw_mcp.server._app import mcp

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


@pytest.mark.parametrize("live_names", [[], ["trw_masked_subset"], ["trw_unmanifested", "external"]])
def test_manifest_is_exact_authority_regardless_of_live_surface(monkeypatch, live_names):
    listing = AsyncMock(return_value=[SimpleNamespace(name=name) for name in live_names])
    monkeypatch.setattr(mcp, "list_tools", listing)
    monkeypatch.setattr(registry, "eligible_tool_names", lambda: {"trw_z", "trw_a", "non_trw"})
    assert codex._registered_trw_tool_names() == ["trw_a", "trw_z"]
    listing.assert_not_called()


def test_generate_config_never_dispatches_live_listing_and_preserves_merge(tmp_path, monkeypatch):
    listing = AsyncMock(side_effect=AssertionError("bootstrap must not dispatch middleware"))
    monkeypatch.setattr(mcp, "list_tools", listing)
    expected = sorted(name for name in registry.eligible_tool_names() if name.startswith("trw_"))
    assert expected
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'model = "operator-model"\n[mcp_servers.trw]\n'
        'enabled_tools = ["external_enabled", "trw_unmanifested"]\n'
        f'disabled_tools = ["external_disabled", "trw_unmanifested", "{expected[0]}"]\n'
        '[[skills.config]]\npath = ".agents/skills/trw-deliver"\nenabled = false\n'
    )
    result = codex.generate_codex_config(tmp_path)
    assert result["errors"] == []
    config = tomllib.loads((config_dir / "config.toml").read_text())
    server = config["mcp_servers"]["trw"]
    assert server["enabled_tools"] == sorted([*expected, "external_enabled"])
    assert server["disabled_tools"] == ["external_disabled", "trw_unmanifested"]
    assert config["model"] == "operator-model"
    assert {"path": ".agents/skills/trw-deliver", "enabled": False} in config["skills"]["config"]
    assert "profiles" not in config
    assert not (tmp_path / ".git").exists()
    listing.assert_not_called()


_HOOK_DIRS = (".claude/hooks", ".codex/hooks")
_REFERENCE_RE = re.compile(r"(\.(?:claude|codex)/hooks/[A-Za-z0-9_.\-]+)")
_SOURCED_RE = re.compile(r"\$_hook_dir/([A-Za-z0-9_.\-]+\.sh)")


def _codex_project(tmp_path: Path, *, hooks_feature: bool) -> Path:
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    if hooks_feature:
        (repo / ".codex").mkdir()
        (repo / ".codex" / "config.toml").write_text("[features]\nhooks = true\n", encoding="utf-8")
    result = init_project(repo, ide="codex")
    assert result["errors"] == []
    return repo


def _written_hook_files(repo: Path) -> set[str]:
    return {
        path.relative_to(repo).as_posix()
        for rel in _HOOK_DIRS
        if (repo / rel).is_dir()
        for path in (repo / rel).iterdir()
        if path.is_file()
    }


def _referenced_hook_files(repo: Path) -> set[str]:
    """Scripts ``.codex/hooks.json`` runs, plus the sibling helpers each of them sources."""
    hooks_json = repo / ".codex" / "hooks.json"
    referenced = set(_REFERENCE_RE.findall(hooks_json.read_text(encoding="utf-8"))) if hooks_json.is_file() else set()
    frontier = set(referenced)
    while frontier:
        rel = frontier.pop()
        script = repo / rel
        if not script.is_file():
            continue
        for helper in _SOURCED_RE.findall(script.read_text(encoding="utf-8")):
            sibling = f"{Path(rel).parent.as_posix()}/{helper}"
            if sibling not in referenced:
                referenced.add(sibling)
                frontier.add(sibling)
    return referenced


def _manifest_paths(repo: Path) -> set[str]:
    manifest = _read_manifest(repo)
    assert manifest is not None
    hashes = manifest["content_hashes"]
    assert isinstance(hashes, dict)
    return {_manifest_key_path(key) for key in hashes}


@pytest.mark.parametrize("hooks_feature", [False, True], ids=["codex-hooks-off", "codex-hooks-on"])
def test_codex_writes_only_referenced_hooks(tmp_path: Path, hooks_feature: bool) -> None:
    """FR07: no hook script codex does not run, and no written hook script the manifest misses."""
    repo = _codex_project(tmp_path, hooks_feature=hooks_feature)
    written = _written_hook_files(repo)

    assert written, "codex always gets its PostToolUse telemetry hook, so the scan must see a file"
    assert written - _referenced_hook_files(repo) == set(), "hook scripts codex never runs were written"
    assert written - _manifest_paths(repo) == set(), "hook scripts missing from managed-artifacts.yaml"
    claude_scripts = {rel for rel in written if rel.startswith(".claude/hooks/")}
    if hooks_feature:
        assert ".claude/hooks/session-start.sh" in claude_scripts
        assert ".claude/hooks/lib-trw.sh" in claude_scripts, "a sourced helper of a run script still ships"
    else:
        assert claude_scripts == set(), "the six .claude/hooks scripts ship only when codex hooks are on"


def test_update_withdraws_scripts_codex_stopped_running(tmp_path: Path) -> None:
    """FR07 on update: turning codex hooks off sweeps the unedited scripts and their manifest entries."""
    repo = _codex_project(tmp_path, hooks_feature=True)
    edited = repo / ".claude" / "hooks" / "stop-ceremony.sh"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")
    config = repo / ".codex" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("hooks = true", "hooks = false"), encoding="utf-8")
    assert not codex.codex_hooks_enabled(repo), "fixture must actually turn codex hooks off"

    result = update_project(repo)

    assert result["errors"] == []
    remaining = {rel for rel in _written_hook_files(repo) if rel.startswith(".claude/hooks/")}
    assert remaining == {".claude/hooks/stop-ceremony.sh"}, "only the user-edited copy survives"
    assert ".claude/hooks/session-start.sh" in result.get("removed", [])
    assert not {rel for rel in _manifest_paths(repo) if rel.startswith(".claude/hooks/")}
