"""PRD-CORE-312 FR03 (stale half): a recall stub carries ``flag: "stale"`` when its last stored check failed.

The unvalidated half is deferred (UF-MEM-02): it needs a new age knob and would flag most of a hand-written corpus.
The stale flag costs no config, no I/O, and not one byte on a row that is not stale.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._recall_presenter import present, stub

_ROW = {"id": "L-1", "summary": "a lesson"}


@pytest.mark.parametrize("status", ["stale", "last_known_failure"])
def test_a_row_whose_last_check_failed_is_flagged_stale(status: str) -> None:
    assert stub({**_ROW, "verification_status": status})["flag"] == "stale"


@pytest.mark.parametrize("status", ["verified", "last_known_pass", "unknown", "", None])
def test_any_other_row_has_no_flag_key_at_all(status: str | None) -> None:
    rendered = stub({**_ROW, "verification_status": status})

    assert "flag" not in rendered, "an unflagged stub must not pay for the key"
    assert set(rendered) == {"id", "claim"}


def test_a_row_without_a_status_is_unflagged() -> None:
    assert "flag" not in stub(_ROW)


def test_the_flag_survives_the_byte_budgeted_envelope() -> None:
    rows = [{**_ROW, "id": "L-1", "verification_status": "stale"}, {**_ROW, "id": "L-2"}]
    envelope: dict[str, object] = {}

    stubs = present(envelope, rows, byte_budget=10_000)

    flagged = {s["id"]: s.get("flag") for s in stubs}
    assert flagged == {"L-1": "stale", "L-2": None}
