"""The 9.1.0 install that deleted 17 Claude Code hooks, and the untracked-file guard that kept TRW's own output.

Observed 2026-10-05 in three fresh projects (no commits, everything untracked; clients claude-code, codex,
antigravity-cli). The bootstrap's ``auth login`` writes ``.trw/config.yaml`` before the installer runs, so
``init-project --ide claude-code`` found a config with no ``target_platforms`` key. The recorder read the absent key
as ``["claude-code"]``, saw nothing to add, and wrote nothing; every reader reads an absent key as "no clients".
``update-project --ide codex`` then resolved ``[codex]`` alone, and the hook sweep withdrew every Claude Code hook
that settings.json still registered. ``doctor``'s ``hook_family`` said SKIP and the installer said "ready".

The same runs warned "kept .trw/config.yaml: it has uncommitted changes" on every pass: in a repository with no
commits TRW's own output is untracked, and nothing proved those bytes were TRW's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap._hook_closure import settings_hook_refs
from trw_mcp.bootstrap._ide_targets_finalize import _update_config_target_platforms
from trw_mcp.bootstrap._init_project import init_project
from trw_mcp.bootstrap._update_project import update_project
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.server._doctor_hook_family import hook_family_row

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_BUNDLED_HOOKS = {p.name for p in (_DATA_DIR / "hooks").glob("*.sh")}
# What the bootstrap's `trw-mcp auth login` leaves before the installer runs: platform keys, no client record.
_AUTH_ONLY_CONFIG = 'platform_org_name: "Example"\nplatform_urls:\n- "https://api.example.invalid"\n'


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_ide_detection(monkeypatch)


def _uncommitted_repo(root: Path, *, auth_config: bool) -> Path:
    """A real git repository with no commits, as `git init` leaves it (every file untracked)."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    if auth_config:
        (root / ".trw").mkdir()
        (root / ".trw" / "config.yaml").write_text(_AUTH_ONLY_CONFIG, encoding="utf-8")
    return root


def _install_like_the_installer(root: Path) -> list[dict[str, list[str]]]:
    """install-trw.py's Step 5 for three clients: init for the first, a per-client update for each other."""
    results = [init_project(root, ide="claude-code")]
    results += [update_project(root, ide=client) for client in ("codex", "antigravity-cli")]
    for result in results:
        assert not result["errors"], result["errors"]
    return results


def _missing_registered_hooks(root: Path) -> list[str]:
    return sorted(name for name in settings_hook_refs(root) if not (root / ".claude" / "hooks" / name).is_file())


def _recorded(root: Path) -> list[str]:
    return list(
        yaml.safe_load((root / ".trw" / "config.yaml").read_text(encoding="utf-8")).get("target_platforms") or []
    )


@pytest.mark.integration
def test_an_install_over_an_auth_only_config_keeps_every_hook_settings_json_registers(tmp_path: Path) -> None:
    root = _uncommitted_repo(tmp_path / "project", auth_config=True)

    results = _install_like_the_installer(root)

    assert settings_hook_refs(root) & _BUNDLED_HOOKS, "fixture: settings.json must register TRW hooks"
    assert _missing_registered_hooks(root) == []
    removed = [p for r in results for p in (*r.get("retired", []), *r.get("cleaned", []))]
    assert not [p for p in removed if p.startswith(".claude/hooks/")], removed
    assert _recorded(root) == ["claude-code", "codex", "antigravity-cli"]


@pytest.mark.integration
def test_untracked_files_holding_trws_own_last_write_are_refreshed_not_kept(tmp_path: Path) -> None:
    root = _uncommitted_repo(tmp_path / "project", auth_config=False)

    results = _install_like_the_installer(root)

    kept = [entry for r in results for entry in r.get("preserved", []) if entry.endswith("(uncommitted_changes)")]
    assert kept == [], kept


@pytest.mark.integration
def test_a_user_edit_to_an_untracked_file_is_still_kept(tmp_path: Path) -> None:
    root = _uncommitted_repo(tmp_path / "project", auth_config=False)
    assert not init_project(root, ide="claude-code")["errors"]
    config = root / ".trw" / "config.yaml"
    edited = config.read_text(encoding="utf-8") + "# my own note\n"
    config.write_text(edited, encoding="utf-8")

    result = update_project(root, ide="codex")  # would record codex in config.yaml

    assert not result["errors"], result["errors"]
    assert config.read_text(encoding="utf-8") == edited
    assert ".trw/config.yaml (uncommitted_changes)" in result["preserved"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("absent_means", "requested", "recorded"),
    [
        (("claude-code",), ["claude-code"], ["claude-code"]),  # the 9.1.0 no-op: now written
        (("claude-code",), ["codex"], ["claude-code", "codex"]),  # legacy pre-record install: unchanged
        ((), ["codex"], ["codex"]),  # init-project: exactly what was asked, as a fresh config records
    ],
)
def test_an_absent_target_platforms_key_is_always_written(
    tmp_path: Path, absent_means: tuple[str, ...], requested: list[str], recorded: list[str]
) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(_AUTH_ONLY_CONFIG, encoding="utf-8")

    _update_config_target_platforms(tmp_path, requested, {}, absent_means=absent_means)

    assert _recorded(tmp_path) == recorded


@pytest.mark.integration
def test_an_absent_key_with_nothing_to_record_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(_AUTH_ONLY_CONFIG, encoding="utf-8")

    _update_config_target_platforms(tmp_path, [], {}, absent_means=())

    assert (tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8") == _AUTH_ONLY_CONFIG


def _settings(root: Path, hooks: list[str]) -> None:
    commands = ",".join(f'{{"command": "$CLAUDE_PROJECT_DIR/.claude/hooks/{name}"}}' for name in hooks)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        f'{{"hooks": {{"SessionStart": [{{"hooks": [{commands}]}}]}}}}', encoding="utf-8"
    )


@pytest.mark.integration
def test_doctor_hook_family_fails_when_settings_registers_missing_trw_hooks(tmp_path: Path) -> None:
    _settings(tmp_path, ["session-start.sh", "stop-ceremony.sh", "my-own-hook.sh"])
    (tmp_path / ".claude" / "hooks").mkdir()

    status, message = hook_family_row(tmp_path)

    assert status == "FAIL", message
    assert "session-start.sh" in message and "stop-ceremony.sh" in message
    assert "my-own-hook.sh" not in message, "a project-owned hook is never this row's FAIL"
    assert "update-project" in message


@pytest.mark.integration
def test_doctor_hook_family_only_warns_when_a_project_owned_registered_hook_is_missing(tmp_path: Path) -> None:
    _settings(tmp_path, ["my-own-hook.sh", "other-own.sh"])
    (tmp_path / ".claude" / "hooks").mkdir()

    status, message = hook_family_row(tmp_path)

    assert status == "WARN", message
    assert "my-own-hook.sh" in message and "other-own.sh" in message


@pytest.mark.integration
def test_doctor_hook_family_still_skips_a_project_with_no_claude_code_hooks(tmp_path: Path) -> None:
    status, _message = hook_family_row(tmp_path)

    assert status == "SKIP"
