"""PRD-CORE-354 FR09(c): the statusLine is opt-in, also for a project installed for the first time.

A first install copies the whole ``settings.json`` template, and the template carries TRW's statusLine (it is
where the entry to install is read from). With ``claude_code_statusline`` absent the reconcile rule is "never
add, never remove", so the copied entry stayed and every new install got a status line, although the
requirement, the changelog and the client documentation all said new installs get none. An update that
recreates a deleted settings file copied the template the same way. The merge tests could not see either: they
start from a settings file that already exists. These drive ``init_project`` and ``update_project`` themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._settings_merge import STATUSLINE_CONFIG_KEY, apply_statusline_registration, is_trw_statusline

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Detect clients from the project only, and keep the user-level settings lookup off the real home."""
    isolate_ide_detection(monkeypatch)
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "home", lambda: home)


def _settings(root: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return loaded


def test_first_install_without_the_key_adds_no_statusline(fake_git_repo: Path) -> None:
    result = init_project(fake_git_repo, ide="claude-code")

    assert not result["errors"]
    settings = _settings(fake_git_repo)
    assert "statusLine" not in settings
    assert settings["hooks"], "the rest of the template was installed"
    assert (fake_git_repo / ".claude" / "hooks" / "statusline.sh").is_file(), "the script ships either way"
    assert STATUSLINE_CONFIG_KEY not in (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_a_second_install_still_adds_none(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="claude-code")

    init_project(fake_git_repo, ide="claude-code")

    assert "statusLine" not in _settings(fake_git_repo)


def test_first_install_with_the_key_true_adds_it(fake_git_repo: Path) -> None:
    """The project chose it before installing: the template's entry stays."""
    (fake_git_repo / ".trw").mkdir()
    (fake_git_repo / ".trw" / "config.yaml").write_text(f"{STATUSLINE_CONFIG_KEY}: true\n", encoding="utf-8")

    result = init_project(fake_git_repo, ide="claude-code")

    assert not result["errors"]
    assert is_trw_statusline(_settings(fake_git_repo).get("statusLine"))


def test_opting_in_after_a_first_install_adds_it(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="claude-code")
    config = fake_git_repo / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + f"\n{STATUSLINE_CONFIG_KEY}: true\n", encoding="utf-8")

    changed = apply_statusline_registration(fake_git_repo)

    assert changed is True
    assert is_trw_statusline(_settings(fake_git_repo).get("statusLine"))


def test_an_existing_settings_file_with_trws_statusline_keeps_it(fake_git_repo: Path) -> None:
    """Not a first install: a display the project already has is never removed by an absent key."""
    init_project(fake_git_repo, ide="claude-code")
    settings_path = fake_git_repo / ".claude" / "settings.json"
    template = json.loads((Path(__file__).resolve().parents[1] / "src/trw_mcp/data/settings.json").read_text())
    settings = _settings(fake_git_repo)
    settings["statusLine"] = template["statusLine"]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    init_project(fake_git_repo, ide="claude-code")

    assert is_trw_statusline(_settings(fake_git_repo).get("statusLine"))


def test_an_update_that_recreates_the_settings_file_adds_no_statusline(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="claude-code")
    (fake_git_repo / ".claude" / "settings.json").unlink()

    result = update_project(fake_git_repo)

    assert not result["errors"]
    settings = _settings(fake_git_repo)
    assert settings["hooks"], "the update did recreate the file from the template"
    assert "statusLine" not in settings
