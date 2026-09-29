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
    """Non-fd integers stand in as test doubles: this row only ever calls ``len()`` on the list."""
    from trw_mcp import _checkout_access

    monkeypatch.setattr(_checkout_access, "_race_loser_fds", [111, 222, 333])

    row = _row(tmp_path)

    assert row.status == "PASS"
    assert "3 race-loser" in row.message
