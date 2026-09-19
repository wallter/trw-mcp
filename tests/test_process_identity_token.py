"""The process birth token must not depend on the reader's locale or timezone (Codex review 2, P2).

Two MCP server processes for the same client compare the token literally. On
macOS it used to be ``ps -o lstart=`` display text, rendered in whatever locale
and timezone the server inherited, so one process read under two environments
produced two different tokens and reconnect adoption failed.
"""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
import time

import pytest

_PROBE = (
    "import locale, sys\n"
    "try:\n"
    "    locale.setlocale(locale.LC_ALL, '')\n"
    "except locale.Error:\n"
    "    pass\n"
    "from trw_mcp.state._process_identity import process_start_time\n"
    "print(process_start_time(int(sys.argv[1])))\n"
)


def _token_under(pid: int, lc_all: str, tz: str) -> str:
    env = {**os.environ, "LC_ALL": lc_all, "LANG": lc_all, "TZ": tz}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(pid)], capture_output=True, text=True, env=env, check=True, timeout=60
    )
    return proc.stdout.strip()


def test_token_is_identical_across_locale_and_timezone() -> None:
    pid = os.getpid()
    tokens = {
        _token_under(pid, "C", "UTC"),
        _token_under(pid, "fr_FR.UTF-8", "Asia/Tokyo"),
        _token_under(pid, "de_DE.UTF-8", "America/Los_Angeles"),
    }
    assert len(tokens) == 1, tokens
    (token,) = tokens
    assert token.isdigit(), token


def test_token_differs_for_a_different_process() -> None:
    # pid 1 (init / launchd) started long before this test process.
    assert _token_under(1, "C", "UTC") != _token_under(os.getpid(), "C", "UTC")


# --- Codex review 3, F7: whole-second resolution let a same-second pid reuse match


def _ps_epoch_seconds(pid: int) -> int:
    text = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "TZ": "UTC", "PATH": "/bin:/usr/bin"},
        check=True,
    ).stdout.strip()
    return calendar.timegm(time.strptime(text, "%a %b %d %H:%M:%S %Y"))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel start timeval")
def test_darwin_token_is_microseconds_since_epoch() -> None:
    from trw_mcp.state._process_identity import process_start_time

    process_start_time.cache_clear()
    token = process_start_time(os.getpid())
    assert token is not None and token.isdigit(), token
    # Six digits below the second that ``ps -o lstart=`` reports, so two births
    # within one second no longer share a token.
    assert int(token) // 1_000_000 == _ps_epoch_seconds(os.getpid()), token


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel start timeval")
def test_darwin_tokens_distinguish_processes_born_in_the_same_second() -> None:
    from trw_mcp.state._process_identity import process_start_time

    children = [subprocess.Popen(["sleep", "5"]) for _ in range(2)]
    try:
        process_start_time.cache_clear()
        first, second = (process_start_time(child.pid) for child in children)
        process_start_time.cache_clear()
        assert process_start_time(children[0].pid) == first  # stable per process
    finally:
        for child in children:
            child.kill()
            child.wait()
    assert first is not None and second is not None
    assert first != second


def test_absent_pid_has_no_token() -> None:
    from trw_mcp.state._process_identity import process_start_time

    assert process_start_time(2**31 - 1) is None
