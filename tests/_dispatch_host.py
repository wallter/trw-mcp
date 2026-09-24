"""Host-dependent setup shared by the dispatch runner tests."""

from __future__ import annotations

import sys

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
