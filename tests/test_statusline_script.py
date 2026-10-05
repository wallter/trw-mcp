"""PRD-CORE-354 FR05: statusline.sh prints exactly one line and exits 0, always."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from trw_mcp.bootstrap._utils import _DATA_DIR

_SCRIPT = Path(_DATA_DIR) / "hooks" / "statusline.sh"
_FALLBACK = "TRW · status unavailable"


def _stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    cli = bin_dir / "trw-mcp"
    cli.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    cli.chmod(0o755)


def _run(project: Path, stdin: str = '{"session_id": "sess-1"}') -> subprocess.CompletedProcess[str]:
    # PATH is pinned to system dirs so the developer's own trw-mcp never leaks in.
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(project), "HOME": str(project)}
    return subprocess.run(
        ["/bin/sh", str(_SCRIPT)], input=stdin, capture_output=True, text=True, env=env, timeout=30, check=False
    )


def _one_line(proc: subprocess.CompletedProcess[str]) -> str:
    assert proc.returncode == 0
    assert proc.stdout.endswith("\n")
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].strip()
    return lines[0]


def test_success_prints_cli_line_and_passes_session_id(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", 'echo "TRW sid=$4 args=$*"')
    line = _one_line(_run(tmp_path))
    assert "sid=" in line
    assert "local status --format line --session-id sess-1 --cache-ttl 5" in line


def test_session_id_is_read_from_nested_stdin_json(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", 'echo "$*"')
    line = _one_line(_run(tmp_path, '{"workspace": {"project_dir": "x"}, "session_id": "abc-123"}'))
    assert "--session-id abc-123" in line


def test_launcher_sibling_from_mcp_json_wins_over_venv(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", "echo venv")
    _stub(tmp_path / "launcher", "echo sibling")
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"trw": {"command": "%s"}}}' % (tmp_path / "launcher" / "trw-mcp-proxy"), encoding="utf-8"
    )
    assert _one_line(_run(tmp_path)) == "sibling"


def test_only_first_line_is_printed(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", 'printf "\\n  first\\nsecond\\n"')
    assert _one_line(_run(tmp_path)) == "  first"


def test_hung_cli_is_killed_and_prints_fallback(tmp_path: Path) -> None:
    """A CLI that never returns must not freeze the status line (bounded wait)."""
    _stub(tmp_path / ".venv" / "bin", "sleep 30; echo late")
    env = {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "HOME": str(tmp_path),
        "TRW_STATUSLINE_TIMEOUT_TICKS": "5",
    }
    started = time.monotonic()
    proc = subprocess.run(
        ["/bin/sh", str(_SCRIPT)],
        input='{"session_id": "s"}',
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert _one_line(proc) == _FALLBACK
    assert time.monotonic() - started < 10


def test_missing_cli_prints_fallback(tmp_path: Path) -> None:
    assert _one_line(_run(tmp_path)) == _FALLBACK


def test_failing_cli_prints_fallback(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", 'echo "partial"; exit 1')
    assert _one_line(_run(tmp_path)) == _FALLBACK


def test_unsupported_flag_cli_prints_fallback(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", 'echo "error: unrecognized arguments: --format" >&2; exit 2')
    assert _one_line(_run(tmp_path)) == _FALLBACK


def test_empty_output_and_garbage_stdin_print_fallback(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", "exit 0")
    assert _one_line(_run(tmp_path, "not json at all")) == _FALLBACK


def test_unreachable_project_dir_prints_fallback(tmp_path: Path) -> None:
    proc = _run(tmp_path / "does-not-exist")
    assert _one_line(proc) == _FALLBACK
    assert os.path.isfile(_SCRIPT)


def test_statusline_command_is_not_counted_as_a_hook_registration() -> None:
    """The carrier scan treats ``statusLine`` as a render surface, not a hook event."""
    from tests._hook_carriers import _commands

    config = {
        "statusLine": {"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh"'},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]},
    }
    assert _commands(config) == ["stop.sh"]


def _status_line_file(project: Path, sid: str, text: str, age: float = 0) -> Path:
    d = project / ".trw" / "runtime" / "status"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.line"
    f.write_text(text + "\n", encoding="utf-8")
    if age:
        old = time.time() - age
        os.utime(f, (old, old))
    return f


_CLI_BODY = 'echo "from-cli"'


def test_fresh_line_file_is_printed_without_running_cli(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", _CLI_BODY)
    _status_line_file(tmp_path, "sess-1", "cached line")
    assert _one_line(_run(tmp_path)) == "cached line"


def test_stale_line_file_falls_through_to_cli(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", _CLI_BODY)
    _status_line_file(tmp_path, "sess-1", "cached line", age=60)
    assert _one_line(_run(tmp_path)) == "from-cli"


def test_invalid_session_id_skips_fast_path(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", _CLI_BODY)
    _status_line_file(tmp_path, "a", "cached line")
    assert _one_line(_run(tmp_path, '{"session_id": "../status/a"}')) == "from-cli"


def test_non_numeric_ticks_still_bounded(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", "sleep 60; echo late")
    env = {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "HOME": str(tmp_path),
        "TRW_STATUSLINE_TIMEOUT_TICKS": "abc",
    }
    started = time.monotonic()
    proc = subprocess.run(
        ["/bin/sh", str(_SCRIPT)], input="{}", capture_output=True, text=True, env=env, timeout=20, check=False
    )
    assert _one_line(proc) == _FALLBACK
    assert time.monotonic() - started < 15


def test_mcp_json_that_is_not_a_regular_file_is_ignored(tmp_path: Path) -> None:
    _stub(tmp_path / ".venv" / "bin", _CLI_BODY)
    os.mkfifo(tmp_path / ".mcp.json")
    assert _one_line(_run(tmp_path)) == "from-cli"
