"""E2E-INC-051 (b) and (c): the update-project preview reports what the real run reports, and user bytes after the block survive a sync."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    assert not init_project(root, ide="claude-code").get("errors")
    return root


@pytest.mark.usefixtures("no_memory_daemon")
def test_the_dry_run_and_the_real_run_report_the_same_files(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    dry = update_project(project, dry_run=True)
    real = update_project(project)

    for key in ("created", "updated", "preserved", "cleaned"):
        assert sorted(dry.get(key, [])) == sorted(real.get(key, [])), key
    assert any(path.endswith(".trw/learnings/index.yaml") for path in dry["preserved"])


@pytest.mark.usefixtures("no_memory_daemon")
def test_instructions_sync_keeps_the_users_text_and_final_newline_after_the_block(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    # The sync resolves its target from the project root, so point it at THIS project (not the checkout running the test).
    monkeypatch.chdir(project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    agents = project / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n<!-- user note -->\n", encoding="utf-8")
    before = agents.read_bytes()

    execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "claude-code")

    assert agents.read_bytes().endswith(b"<!-- user note -->\n")
    assert agents.read_bytes() == before


def _kept_lines(result: dict[str, list[str]], root: Path) -> list[str]:
    from trw_mcp.server._update_report import kept_files

    return [f"{path}: {why}" for path, why in kept_files(result, root)]


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every project file but .git and .trw/runtime (the git post-commit sidecar writes there on its own clock)."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".trw/runtime" not in p.as_posix()
    }


@pytest.mark.usefixtures("no_memory_daemon")
@pytest.mark.parametrize("case", ["dirty_stale", "dirty_user_edit_only", "committed_stale"])
def test_a_settings_file_is_previewed_as_the_real_run_treats_it(tmp_path: Path, case: str) -> None:
    """A user edit AND an outdated TRW registration: the real run keeps the user's keys and updates TRW's entries."""
    from tests.test_update_kept_hook_registration import SETTINGS, _git, _outdate, installed_project
    from trw_mcp.bootstrap import update_project

    root = installed_project(tmp_path)
    _outdate(root, edit_hook=False, user_key=True)
    if case == "dirty_user_edit_only":  # TRW has nothing to change in the file
        _git(root, "checkout", "-q", "--", SETTINGS)
        (root / SETTINGS).write_text((root / SETTINGS).read_text(encoding="utf-8").replace("{", '{"x": 1,', 1))
    if case == "committed_stale":
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "stale registration")

    before = _snapshot(root)
    dry = update_project(root, dry_run=True)
    assert _snapshot(root) == before, "the preview changed the project"
    real = update_project(root)

    assert not dry["errors"]
    assert not real["errors"]
    for key in ("created", "updated", "preserved", "cleaned"):
        assert sorted(dry.get(key, [])) == sorted(real.get(key, [])), key

    def settings_entry(result: dict[str, list[str]]) -> list[str]:
        return [e.split(" (", 1)[1] for e in result["preserved"] if e.startswith(SETTINGS)]

    expected = {"dirty_stale": ["uncommitted_changed_after_keep)"], "dirty_user_edit_only": ["uncommitted_changes)"]}
    assert settings_entry(real) == settings_entry(dry) == expected.get(case, [])
    assert (SETTINGS in real["updated"]) == (SETTINGS in dry["updated"]) == (case != "dirty_user_edit_only")
    dry_text, real_text = _kept_lines(dry, root), _kept_lines(real, root)
    if case == "dirty_stale":
        assert "would keep your version, then update TRW's own entries" in dry_text[0]
        assert "later steps then changed TRW's own entries" in real_text[0]
    else:
        assert dry_text == real_text
