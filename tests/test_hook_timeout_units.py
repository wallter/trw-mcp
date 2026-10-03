"""E2E-HOOK-TIMEOUT-UNITS: Claude Code reads a hook ``timeout`` in SECONDS.

The bundled values were written as milliseconds (5000/10000/500/3000 = 83 min / 2.8 h hang ceilings). A timed-out
hook is a non-blocking error to Claude Code, so a guard's timeout must also leave room for a loaded machine: a
PreToolUse guard killed early fails OPEN. Every timeout is a whole number of seconds in [5, 60], guards get 60.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

_BUNDLED = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "settings.json"
# The repo's own .claude/settings.json exists only in the monorepo; the public package checks the bundled copy alone.
_FILES = [_BUNDLED, *([MONOREPO_ROOT / ".claude" / "settings.json"] if MONOREPO_ROOT is not None else [])]
_IDS = ["bundled", "repo"][: len(_FILES)]
MAX_SECONDS = 60


def _hooks(path: Path) -> list[tuple[str, str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        (event, str(group.get("matcher", "")), hook["timeout"])
        for event, groups in data.get("hooks", {}).items()
        for group in groups
        for hook in group.get("hooks", [])
        if "timeout" in hook
    ]


@pytest.mark.parametrize("path", _FILES, ids=_IDS)
def test_every_hook_timeout_is_seconds_within_a_sane_ceiling(path: Path) -> None:
    hooks = _hooks(path)
    assert hooks
    bad = [(e, m, t) for e, m, t in hooks if not (isinstance(t, int) and 5 <= t <= MAX_SECONDS)]
    assert bad == [], f"{path}: timeouts are seconds in [5, {MAX_SECONDS}]"


@pytest.mark.parametrize("path", _FILES, ids=_IDS)
def test_pre_tool_guards_get_the_full_ceiling_so_load_never_fails_them_open(path: Path) -> None:
    guards = [(e, m, t) for e, m, t in _hooks(path) if e == "PreToolUse"]
    assert guards and all(t == MAX_SECONDS for _, _, t in guards), guards


def test_update_project_migrates_a_legacy_millisecond_timeout_and_keeps_a_user_value(tmp_path: Path) -> None:
    """The settings merge never rewrites an entry, so existing installs kept 5000 (= 83 min). A TRW hook whose
    timeout exceeds Claude Code's own 600 s default can only be the legacy millisecond value: it takes the bundled
    seconds. A user's deliberate value at or below 600 is left alone."""
    from trw_mcp.bootstrap._settings_merge import _merge_settings_json

    bundled_path = _FILES[0]
    bundled = json.loads(bundled_path.read_text(encoding="utf-8"))
    existing = json.loads(json.dumps(bundled))
    start = existing["hooks"]["SessionStart"][0]["hooks"][0]
    stop = existing["hooks"]["Stop"][0]["hooks"][0]
    start["timeout"], stop["timeout"] = 5000, 120  # legacy ms value / a user's own seconds choice
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    result: dict[str, list[str]] = {"errors": []}
    _merge_settings_json(bundled_path, dest, result)
    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert (
        merged["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]
        == bundled["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]
    )
    assert merged["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 120
    assert result["errors"] == []


def test_a_user_authored_hook_with_a_large_timeout_is_never_touched(tmp_path: Path) -> None:
    """The migration matches TRW's own hooks by COMMAND, never by value: a user's hook at 5000 s stays 5000."""
    from trw_mcp.bootstrap._settings_merge import _merge_settings_json

    bundled_path = _FILES[0]
    existing = json.loads(bundled_path.read_text(encoding="utf-8"))
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "sh ./my-own-hook.sh", "timeout": 5000}]}
    existing["hooks"].setdefault("PreToolUse", []).append(mine)
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    _merge_settings_json(bundled_path, dest, {"errors": []})
    merged = json.loads(dest.read_text(encoding="utf-8"))
    kept = [h for g in merged["hooks"]["PreToolUse"] for h in g["hooks"] if h["command"] == "sh ./my-own-hook.sh"]
    assert kept == [mine["hooks"][0]]


# --- FB-INSTALL-04 (feedback sub_LPf8twni_dwigM-Q): every OTHER place TRW renders a Claude Code hook timeout ---------
#
# E2E-HOOK-TIMEOUT-UNITS converted the two settings.json files only. The CC-03 distill-hint registration
# (`bootstrap/_claude_code_distill_channels.py`) still wrote 3000 (= 50 min on a PreToolUse hook matching every
# Write/Edit), the bundled Claude Code plugin's hooks.json kept 3000/10000/5000/3000, and an upgraded install kept a
# legacy 500 on `user-prompt-submit.sh` because the migration's 600 s floor treats 500 as a user's choice.

_PLUGIN_HOOKS = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "plugin" / "hooks" / "hooks.json"


def test_the_distill_hint_registration_timeout_is_seconds_within_the_ceiling() -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_ENTRY

    timeouts = [hook["timeout"] for hook in _CC03_ENTRY["hooks"]]  # type: ignore[index]
    assert timeouts and all(isinstance(t, int) and 5 <= t <= MAX_SECONDS for t in timeouts), timeouts


def test_every_explicit_timeout_in_the_bundled_plugin_hooks_is_seconds_within_the_ceiling() -> None:
    hooks = _hooks(_PLUGIN_HOOKS)
    assert hooks
    bad = [(e, m, t) for e, m, t in hooks if not (isinstance(t, int) and 5 <= t <= MAX_SECONDS)]
    assert bad == [], f"{_PLUGIN_HOOKS}: timeouts are seconds in [5, {MAX_SECONDS}]"
    assert all(t == MAX_SECONDS for e, _, t in hooks if e == "PreToolUse"), hooks


def test_update_replaces_an_installed_millisecond_distill_hint_registration(tmp_path: Path) -> None:
    """The registration is replaced by identity (the command), so correcting the entry corrects installs."""
    from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_ENTRY
    from trw_mcp.bootstrap._settings_merge import _set_hook_registration

    legacy = json.loads(json.dumps(_CC03_ENTRY))
    legacy["hooks"][0]["timeout"] = 3000
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [legacy]}}, indent=2) + "\n", encoding="utf-8")

    assert _set_hook_registration(settings, "PreToolUse", _CC03_ENTRY, present=True) is True

    (entry,) = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert entry["hooks"][0]["timeout"] == _CC03_ENTRY["hooks"][0]["timeout"]  # type: ignore[index]


def test_update_project_migrates_the_legacy_500_on_user_prompt_submit(tmp_path: Path) -> None:
    """500 is below the 600 s floor, so it read as a user's choice and survived: 500 s is 8.3 minutes on every prompt.
    The migration also takes the exact values TRW itself used to ship, by command."""
    from trw_mcp.bootstrap._settings_merge import _merge_settings_json

    bundled_path = _FILES[0]
    bundled = json.loads(bundled_path.read_text(encoding="utf-8"))
    existing = json.loads(json.dumps(bundled))
    existing["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] = 500
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    _merge_settings_json(bundled_path, dest, {"errors": []})

    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert (
        merged["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"]
        == bundled["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"]
    )


def test_a_users_own_hook_at_500_is_never_touched(tmp_path: Path) -> None:
    """Non-vacuity partner: the legacy values are matched by TRW's command, never by value alone."""
    from trw_mcp.bootstrap._settings_merge import _merge_settings_json

    bundled_path = _FILES[0]
    existing = json.loads(bundled_path.read_text(encoding="utf-8"))
    mine = {"hooks": [{"type": "command", "command": "sh ./my-own-hook.sh", "timeout": 500}]}
    existing["hooks"].setdefault("UserPromptSubmit", []).append(mine)
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    _merge_settings_json(bundled_path, dest, {"errors": []})

    merged = json.loads(dest.read_text(encoding="utf-8"))
    kept = [h for g in merged["hooks"]["UserPromptSubmit"] for h in g["hooks"] if h["command"] == "sh ./my-own-hook.sh"]
    assert kept == mine["hooks"]
