"""PRD-CORE-354 FR06: installer ownership of the Claude Code ``statusLine``.

A ``statusLine`` whose command references ``.claude/hooks/statusline.sh`` is
TRW-owned: init/update adds or rewrites it, opt-out and uninstall remove only it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._settings_merge import (
    _merge_settings_json,
    apply_statusline_registration,
    is_trw_statusline,
)
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.server._uninstall_hook_strips import _strip_trw_claude_settings

_TEMPLATE = Path(_DATA_DIR) / "settings.json"
_CURRENT = json.loads(_TEMPLATE.read_text(encoding="utf-8"))["statusLine"]
_USER = {"type": "command", "command": "~/bin/my-statusline"}
_OLD_TRW = {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh" --legacy'}


def _project(tmp_path: Path, settings: dict[str, object] | None, *, optout: bool = False, optin: bool = False) -> Path:
    (tmp_path / ".claude").mkdir()
    if settings is not None:
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if optout or optin:
        (tmp_path / ".trw").mkdir()
        flag = "true" if optin else "false"
        (tmp_path / ".trw" / "config.yaml").write_text(f"claude_code_statusline: {flag}\n", encoding="utf-8")
    return tmp_path


def _merge(root: Path) -> dict[str, object]:
    result: dict[str, list[str]] = {"errors": [], "updated": [], "created": [], "preserved": []}
    _merge_settings_json(_TEMPLATE, root / ".claude" / "settings.json", result)
    assert not result["errors"]
    loaded: dict[str, object] = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return loaded


def test_template_statusline_is_trw_owned() -> None:
    assert is_trw_statusline(_CURRENT)
    assert not is_trw_statusline(_USER)
    assert not is_trw_statusline(None)


def test_absent_key_adds_no_statusline(tmp_path: Path) -> None:
    """Opt-in: the default display is the trw-ui footer label, so no statusLine row."""
    root = _project(tmp_path, {"env": {"X": "1"}})
    assert "statusLine" not in _merge(root)
    assert apply_statusline_registration(root) is False


def test_true_key_adds_statusline(tmp_path: Path) -> None:
    root = _project(tmp_path, {"env": {"X": "1"}}, optin=True)
    assert _merge(root)["statusLine"] == _CURRENT


def test_absent_key_keeps_an_existing_trw_statusline(tmp_path: Path) -> None:
    """The installer does not install the mod, so an existing display is never silently removed."""
    root = _project(tmp_path, {"statusLine": _CURRENT, "model": "m"})
    merged = _merge(root)
    assert merged["statusLine"] == _CURRENT and merged["model"] == "m"


def test_absent_key_rewrites_an_older_trw_statusline(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _OLD_TRW})
    assert _merge(root)["statusLine"] == _CURRENT


def test_false_key_removal_records_a_note(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _CURRENT}, optout=True)
    result: dict[str, list[str]] = {"errors": [], "updated": [], "created": [], "preserved": []}
    _merge_settings_json(_TEMPLATE, root / ".claude" / "settings.json", result)
    assert "statusLine" not in json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert any("claude_code_statusline" in n for n in result["notes"])


def test_user_statusline_untouched_whatever_the_key(tmp_path: Path) -> None:
    for i, kw in enumerate(({}, {"optin": True}, {"optout": True})):
        d = tmp_path / str(i)
        d.mkdir()
        assert _merge(_project(d, {"statusLine": _USER}, **kw))["statusLine"] == _USER


def test_user_statusline_is_untouched(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _USER})
    assert _merge(root)["statusLine"] == _USER
    assert apply_statusline_registration(root) is False
    assert _strip_trw_claude_settings(json.dumps({"statusLine": _USER}, indent=2) + "\n", root)[0] is False


def test_older_trw_statusline_is_rewritten(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _OLD_TRW}, optin=True)
    assert _merge(root)["statusLine"] == _CURRENT


def test_optout_removes_only_trw_statusline(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _CURRENT, "model": "m"}, optout=True)
    assert "statusLine" not in _merge(root)
    user_root = tmp_path / "u"
    user_root.mkdir()
    user = _project(user_root, {"statusLine": _USER}, optout=True)
    assert _merge(user)["statusLine"] == _USER


def test_optout_on_fresh_template_copy(tmp_path: Path) -> None:
    root = _project(tmp_path, None, optout=True)
    (root / ".claude" / "settings.json").write_text(_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    assert apply_statusline_registration(root) is True
    assert "statusLine" not in json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def test_uninstall_removes_only_trw_statusline() -> None:
    raw = json.dumps({"model": "m", "statusLine": _CURRENT}, indent=2) + "\n"
    changed, out, _ = _strip_trw_claude_settings(raw, Path("."))
    assert changed
    assert json.loads(out) == {"model": "m"}


def test_update_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path, {"statusLine": _OLD_TRW}, optin=True)
    first = _merge(root)
    text = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert _merge(root) == first
    assert (root / ".claude" / "settings.json").read_text(encoding="utf-8") == text
    assert apply_statusline_registration(root) is False


@pytest.mark.parametrize(
    "config", ["", "claude_code_statusline: false\n", "{not yaml: [", "- a\n- b\n", "claude_code_statusline: yes-ish\n"]
)
def test_unreadable_or_non_true_config_adds_nothing(tmp_path: Path, config: str) -> None:
    root = _project(tmp_path, {})
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text(config, encoding="utf-8")
    assert "statusLine" not in _merge(root)


@pytest.mark.parametrize(
    "command",
    [
        'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh"',
        'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh" --legacy',
        '"${CLAUDE_PROJECT_DIR}/.claude/hooks/statusline.sh"',
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh -v --width=5",
    ],
)
def test_trw_command_variants_are_owned(command: str) -> None:
    assert is_trw_statusline({"type": "command", "command": command})


@pytest.mark.parametrize(
    "command",
    [
        "bash ~/.claude/hooks/statusline.sh",
        "/srv/someone/.claude/hooks/statusline.sh",
        'sh "$HOME/.claude/hooks/statusline.sh"',
        "echo $CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh.bak",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh && my-thing",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh; rm -rf x",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh my-thing",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh -v | tee out",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh\nmy-thing",
    ],
)
def test_user_scripts_named_statusline_are_not_owned(command: str) -> None:
    assert not is_trw_statusline({"type": "command", "command": command})


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: dict[str, object] | None) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    if settings is not None:
        (home / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)


def test_user_level_statusline_is_not_shadowed_by_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(tmp_path, monkeypatch, {"statusLine": _USER})
    (tmp_path / "p").mkdir()
    root = _project(tmp_path / "p", {"env": {"X": "1"}})
    assert "statusLine" not in _merge(root)
    assert apply_statusline_registration(root) is False


def test_local_settings_statusline_blocks_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(tmp_path, monkeypatch, None)
    (tmp_path / "p").mkdir()
    root = _project(tmp_path / "p", {})
    (root / ".claude" / "settings.local.json").write_text(json.dumps({"statusLine": _USER}), encoding="utf-8")
    assert "statusLine" not in _merge(root)


def test_existing_trw_entry_removed_with_note_when_user_level_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch, {"statusLine": _USER})
    (tmp_path / "p").mkdir()
    root = _project(tmp_path / "p", {"statusLine": _CURRENT}, optin=True)
    result: dict[str, list[str]] = {"errors": [], "updated": [], "created": [], "preserved": []}
    _merge_settings_json(_TEMPLATE, root / ".claude" / "settings.json", result)
    assert "statusLine" not in json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert any("statusLine" in n for n in result["notes"])


def test_trw_statusline_at_user_level_does_not_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(tmp_path, monkeypatch, {"statusLine": _CURRENT})
    (tmp_path / "p").mkdir()
    assert _merge(_project(tmp_path / "p", {}, optin=True))["statusLine"] == _CURRENT


def test_uninstall_leaves_a_custom_formatted_file_byte_identical() -> None:
    """Custom formatting is never rewritten (uninstall reports the file and fails the run instead,
    see test_uninstall_custom_format_reported), so a TRW statusLine there stays for the user."""
    raw = '{ "model":"m",\n  "statusLine": %s,\n\t"x": 1 }\n' % json.dumps(_CURRENT)
    changed, out, _ = _strip_trw_claude_settings(raw, Path("."))
    assert not changed and out == raw
