"""E2E-HOOK-TIMEOUT-UNITS: Claude Code reads a hook ``timeout`` in SECONDS.

The bundled values were written as milliseconds (5000/10000/500/3000 = 83 min / 2.8 h hang ceilings). A timed-out
hook is a non-blocking error to Claude Code, so a guard's timeout must also leave room for a loaded machine: a
PreToolUse guard killed early fails OPEN. Every timeout is a whole number of seconds in [5, 60], guards get 60.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FILES = [_REPO / "trw-mcp" / "src" / "trw_mcp" / "data" / "settings.json", _REPO / ".claude" / "settings.json"]
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


@pytest.mark.parametrize("path", _FILES, ids=["bundled", "repo"])
def test_every_hook_timeout_is_seconds_within_a_sane_ceiling(path: Path) -> None:
    hooks = _hooks(path)
    assert hooks
    bad = [(e, m, t) for e, m, t in hooks if not (isinstance(t, int) and 5 <= t <= MAX_SECONDS)]
    assert bad == [], f"{path}: timeouts are seconds in [5, {MAX_SECONDS}]"


@pytest.mark.parametrize("path", _FILES, ids=["bundled", "repo"])
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
