"""``trw-mcp doctor`` row for the AG-03 hook location (UF-BOOT-08)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.server._doctor_antigravity_hook import antigravity_hook_row


def _flat(command: str) -> str:
    return json.dumps({"PreToolUse": [{"matcher": "write_file", "command": command}]})


def test_row_passes_when_nothing_is_installed(tmp_path: Path) -> None:
    status, message = antigravity_hook_row(tmp_path)

    assert status == "PASS"
    assert "no legacy TRW AG-03 hook" in message


def test_row_warns_on_a_trw_hook_in_the_legacy_file_agy_does_not_read(tmp_path: Path) -> None:
    legacy = tmp_path / ".antigravitycli" / "hooks.json"
    legacy.parent.mkdir()
    legacy.write_text(_flat("python3 .antigravitycli/hooks/trw_before_edit_telemetry.py"), encoding="utf-8")

    status, message = antigravity_hook_row(tmp_path)

    assert status == "WARN"
    assert ".antigravitycli/hooks.json" in message
    assert ".agents/hooks.json" in message
    assert "never fires" in message


def test_row_warns_when_only_the_legacy_script_remains(tmp_path: Path) -> None:
    script = tmp_path / ".antigravitycli" / "hooks" / "trw_before_edit_telemetry.py"
    script.parent.mkdir(parents=True)
    script.write_text("# legacy\n", encoding="utf-8")

    status, message = antigravity_hook_row(tmp_path)

    assert status == "WARN"
    assert "trw_before_edit_telemetry.py" in message


def test_row_ignores_a_users_own_legacy_hook(tmp_path: Path) -> None:
    legacy = tmp_path / ".antigravitycli" / "hooks.json"
    legacy.parent.mkdir()
    legacy.write_text(_flat("echo USER_PRE_HOOK"), encoding="utf-8")

    status, _ = antigravity_hook_row(tmp_path)

    assert status == "PASS"


def test_row_survives_an_unparseable_legacy_file(tmp_path: Path) -> None:
    legacy = tmp_path / ".antigravitycli" / "hooks.json"
    legacy.parent.mkdir()
    legacy.write_text("{not json", encoding="utf-8")

    status, _ = antigravity_hook_row(tmp_path)

    assert status == "PASS"


def test_row_is_registered_in_the_doctor_catalogue(tmp_path: Path) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    assert ("antigravity_hook", "_check_antigravity_hook") in doctor._CHECKS
    result = doctor._check_antigravity_hook(tmp_path, None)  # type: ignore[arg-type]
    assert result.name == "antigravity_hook"
    assert result.status == "PASS"
