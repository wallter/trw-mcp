"""PRD-CORE-316 P3 (worker-3 review) -- the ``checkout_access`` doctor row.

Before this there was no operator-visible count of the checkout-access pinned-fd cache's
race-loser descriptors (FR02(b)'s stat/open-race losers, and the P0 fix's re-pinned-key
survivors) -- both deliberately never closed, so a build-up over a long-running process was
invisible until someone read the code.

Exercised through the real doctor catalogue (not the sibling in isolation), so a row that exists
but is never registered fails here -- the same convention ``test_doctor_wal_line.py`` uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


def _row(target: Path, name: str = "checkout_access") -> object:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _doctor_core

    results = _doctor_core(target, TRWConfig())
    matches = [r for r in results if r.name == name]
    assert matches, f"doctor produced no {name} row; got {[r.name for r in results]}"
    return matches[0]


def test_checkout_access_row_reports_zero_when_no_race_losers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp import _checkout_access

    monkeypatch.setattr(_checkout_access, "_race_loser_fds", [])

    row = _row(tmp_path)

    assert row.status == "PASS"
    assert "0 race-loser" in row.message


def test_checkout_access_row_reports_the_live_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three descriptors this test opened, never made-up numbers: the autouse cache reset closes whatever
    the list holds, and in a long serial run 111 was a live daemon socket (every later daemon call that
    was handed descriptor 111 then failed as "daemon unreachable")."""
    import os

    from trw_mcp import _checkout_access

    held_file = tmp_path / "race-loser"
    held_file.write_bytes(b"x")
    held = [os.open(held_file, os.O_RDONLY) for _ in range(3)]
    try:
        with monkeypatch.context() as patched:  # undone before the reset fixture's teardown can see the list
            patched.setattr(_checkout_access, "_race_loser_fds", list(held))
            row = _row(tmp_path)
    finally:
        for fd in held:
            os.close(fd)

    assert row.status == "PASS"
    assert "3 race-loser" in row.message
