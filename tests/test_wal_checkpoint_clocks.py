"""PRD-CORE-248 FR04 clause 5 and FR06: the checkpoint clocks the ``memory_wal`` doctor row reads.

The daemon writes three markers beside the store (attempt, effective, reset); trw-mcp only reads them. An absent,
unparseable or future-dated marker is unknown, never a fabricated age, and the three clocks are never confused. The
trigger that once lived beside these readers had no production caller after PRD-CORE-298 and was deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._wal_triggers import (
    checkpoint_marker_path,
    effective_checkpoint_marker_path,
    last_checkpoint_age_seconds,
    last_effective_checkpoint_age_seconds,
    last_reset_checkpoint_age_seconds,
    reset_checkpoint_marker_path,
)

_NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    ("raw", "age"),
    [
        (None, None),
        ("not a number", None),
        (f"{_NOW + 3600}", None),
        (f"{_NOW + 0.5}", 0.0),
        (f"{_NOW - 7200}", 7200.0),
    ],
    ids=["absent", "unparseable", "from the future", "a fraction of a second ahead", "two hours old"],
)
def test_a_marker_reads_as_its_age_or_as_unknown(tmp_path: Path, raw: str | None, age: float | None) -> None:
    db_path = tmp_path / "memory.db"
    if raw is not None:
        checkpoint_marker_path(db_path).write_text(raw, encoding="utf-8")

    got = last_checkpoint_age_seconds(db_path, now=_NOW)

    assert got == (pytest.approx(age) if age is not None else None)


def test_the_three_clocks_read_their_own_markers(tmp_path: Path) -> None:
    """The attempt, effective and reset clocks answer different questions; a fresh one never stands in for another."""
    db_path = tmp_path / "memory.db"
    checkpoint_marker_path(db_path).write_text(f"{_NOW - 60}", encoding="utf-8")
    effective_checkpoint_marker_path(db_path).write_text(f"{_NOW - 3600}", encoding="utf-8")
    reset_checkpoint_marker_path(db_path).write_text(f"{_NOW - 86400}", encoding="utf-8")

    assert last_checkpoint_age_seconds(db_path, now=_NOW) == pytest.approx(60)
    assert last_effective_checkpoint_age_seconds(db_path, now=_NOW) == pytest.approx(3600)
    assert last_reset_checkpoint_age_seconds(db_path, now=_NOW) == pytest.approx(86400)


def test_the_trigger_is_gone_and_nothing_imports_it() -> None:
    """Deleted superseded code stays deleted: the module no longer offers a checkpoint decision of its own."""
    import trw_mcp.state._wal_triggers as module

    assert not hasattr(module, "evaluate_wal_trigger")
    assert "evaluate_wal_trigger" not in module.__all__
