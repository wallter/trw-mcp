"""Host-dependent setup shared by the dispatch runner tests."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest


def unconfined_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a read-only agy stub reach the runner's mechanics on a host with no write-denial wrapper.

    Only macOS has the wrapper, so elsewhere the runner refuses a read-only agy
    dispatch before any posture check or spawn (PRD-CORE-277 AGY-SANDBOX: refuse,
    never widen). A test of what happens after that point runs its stub unconfined
    there; on macOS nothing changes and the stub still runs under the wrapper.
    """
    if sys.platform != "darwin":
        monkeypatch.setattr("trw_mcp.dispatch._runner._needs_host_confinement", lambda _req: False)


def write_stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable bash stub running *body* and return its path."""
    script = tmp_path / name
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return script


def use_argv(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Make the runner execute *argv* regardless of the request's client, unconfined off macOS."""
    unconfined_off_darwin(monkeypatch)

    def _fixed(_req: object, *, confined: bool = False) -> list[str]:
        return argv

    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", _fixed)


def install_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, name: str = "stub-cli") -> Path:
    """Make every dispatch launch an executable bash stub running *body*, unconfined off macOS."""
    script = write_stub(tmp_path, name, body)
    use_argv(monkeypatch, [str(script)])
    return script
