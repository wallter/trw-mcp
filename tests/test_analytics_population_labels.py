"""PRD-FIX-141-FR04 — every generated count names the population it counts.

Three surfaces printed a learning count on 2026-09-16 and none of them said what
it counted: the generated ``.trw/INSTRUCTIONS.md`` said ``0 learnings from 0
prior sessions``, ``trw://learnings/summary`` said ``Total learnings: 9``, and
the store held 1,346 entries (learning L-Rikf). The zero was the worst of the
three, because that file is ``@``-included into every session: it told every
agent the corpus was empty.

These tests drive the real generators over a real SQLite store. The negative
case — an unreadable store must render "not measured", never "0" — is the one
that would have caught the original defect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trw_mcp.state._store_counts import StoreCounts, read_store_counts


def _write_store(trw_dir: Path, *, local: int, synced: int, canaries: int = 0) -> None:
    """Create a memory store holding the requested provenance mix."""
    (trw_dir / "memory").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(trw_dir / "memory" / "memory.db"))
    conn.execute(
        "CREATE TABLE memories (id TEXT PRIMARY KEY, namespace TEXT NOT NULL DEFAULT 'default', "
        "source TEXT DEFAULT 'agent', metadata TEXT DEFAULT '{}')"
    )
    rows = (
        [(f"local{i}", "default", "agent", "{}") for i in range(local)]
        + [(f"sync{i}", "default", "team_sync", "{}") for i in range(synced)]
        + [(f"canary{i}", "default", "agent", '{"system_canary": "true"}') for i in range(canaries)]
    )
    conn.executemany("INSERT INTO memories (id, namespace, source, metadata) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# The one reader
# ---------------------------------------------------------------------------


def test_store_counts_split_local_from_synced(tmp_path: Path) -> None:
    """The split is the whole point: one total for two populations is the defect."""
    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=22, synced=1324)

    counts = read_store_counts(trw_dir)

    assert counts == StoreCounts(total=1346, local=22, synced=1324)


def test_store_counts_exclude_system_canaries(tmp_path: Path) -> None:
    """Framework instrumentation is not knowledge and must not inflate the inventory."""
    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=5, synced=0, canaries=10)

    counts = read_store_counts(trw_dir)

    assert counts is not None
    assert counts.total == 5


def test_store_counts_are_namespace_scoped(tmp_path: Path) -> None:
    """A file-wide count is not this project's inventory."""
    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=3, synced=0)
    conn = sqlite3.connect(str(trw_dir / "memory" / "memory.db"))
    conn.execute("INSERT INTO memories (id, namespace, source, metadata) VALUES ('other', 'team:acme', 'agent', '{}')")
    conn.commit()
    conn.close()

    counts = read_store_counts(trw_dir)

    assert counts is not None
    assert counts.total == 3


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(lambda d: None, id="no-store-file"),
        pytest.param(
            lambda d: (d / "memory").mkdir(parents=True) or (d / "memory" / "memory.db").write_bytes(b"not sqlite"),
            id="corrupt-store",
        ),
    ],
)
def test_an_unmeasurable_store_is_none_and_never_zero(tmp_path: Path, prepare: object) -> None:
    """``None`` and ``StoreCounts(0, 0, 0)`` are different answers, deliberately."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    prepare(trw_dir)  # type: ignore[operator]

    assert read_store_counts(trw_dir) is None


def test_a_genuinely_empty_store_counts_zero_not_none(tmp_path: Path) -> None:
    """The other half of the distinction: a read store with no rows IS zero."""
    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=0, synced=0)

    assert read_store_counts(trw_dir) == StoreCounts(total=0, local=0, synced=0)


# ---------------------------------------------------------------------------
# The generated instruction claim
# ---------------------------------------------------------------------------


def test_the_session_start_claim_names_both_populations(monkeypatch: pytest.MonkeyPatch) -> None:
    """The line every session reads states the store total AND its provenance split."""
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (2, 22))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: StoreCounts(total=1346, local=22, synced=1324))

    claim = routing._format_learning_session_claim()

    assert "1346 learnings in this project's store" in claim
    assert "22 recorded locally across 2 prior sessions" in claim
    assert "1324 pulled from team sync" in claim


def test_the_claim_never_says_zero_when_the_store_holds_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact 2026-09-16 regression: analytics 0/0 over a 1,346-entry store.

    ``analytics.yaml`` is written AFTER the instruction render on a first sync,
    so the counters legitimately read 0/0 while the store is full. The claim
    must lead with what was measured, not with the counter that had not been
    written yet.
    """
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (0, 0))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: StoreCounts(total=1346, local=22, synced=1324))

    claim = routing._format_learning_session_claim()

    assert "0 learnings" not in claim
    assert "0 prior sessions" not in claim
    assert "1346 learnings in this project's store" in claim


def test_an_unmeasured_store_says_so_instead_of_claiming_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-open renders an honest "not measured", never a fabricated count."""
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (2, 22))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: None)

    claim = routing._format_learning_session_claim()

    assert "could not be measured" in claim
    assert "0 learnings" not in claim


def test_a_local_only_store_does_not_print_a_meaningless_sync_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing synced, ``total`` and ``local`` are the same number.

    Printing both would be the mirror of the original defect: noise that reads
    like two populations when there is one.
    """
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (1, 4))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: StoreCounts(total=4, local=4, synced=0))

    claim = routing._format_learning_session_claim()

    assert claim == "4 learnings recorded locally in this project's store across 1 prior session"


def test_the_store_count_is_cached_within_a_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bounded query per turn: the instruction surface renders several sections."""
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=3, synced=0)
    monkeypatch.setattr(routing._paths, "resolve_project_root", lambda: tmp_path)

    calls: list[Path] = []
    real = read_store_counts

    def counting(path: Path, **kwargs: object) -> StoreCounts | None:
        calls.append(path)
        return real(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("trw_mcp.state._store_counts.read_store_counts", counting)
    routing._store_counts_cache.set(None)

    routing._load_store_counts()
    routing._load_store_counts()
    routing._load_store_counts()

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# The learnings-summary resource
# ---------------------------------------------------------------------------


def test_learnings_summary_analytics_block_names_its_populations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Total learnings: 9`` was the local counter, printed as if it were the store."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.resources import config as resource_config
    from trw_mcp.resources.config import _build_learnings_summary

    # The high-impact block boots the real backend, which would migrate (and so
    # rewrite) the minimal fixture store this test builds. The Analytics block is
    # what FR04 changed; stub the neighbour rather than the surface under test.
    monkeypatch.setattr(resource_config, "list_active_learnings", lambda *a, **k: [])

    trw_dir = tmp_path / ".trw"
    _write_store(trw_dir, local=22, synced=1324)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context" / "analytics.yaml").write_text(
        "sessions_tracked: 2\ntotal_learnings: 22\navg_learnings_per_session: 0.96\n",
        encoding="utf-8",
    )

    body = _build_learnings_summary(trw_dir, TRWConfig())

    assert "- Entries in this project's store: 1346" in body
    assert "recorded locally: 22" in body
    assert "pulled from team sync: 1324" in body
    assert "Sessions tracked (delivered): 2" in body
    assert "Total learnings:" not in body, "the unqualified label is what made the number unreadable"


def test_learnings_summary_reports_an_unreadable_store_as_not_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative case: no store file must not render as an inventory of zero."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.resources import config as resource_config
    from trw_mcp.resources.config import _build_learnings_summary

    monkeypatch.setattr(resource_config, "list_active_learnings", lambda *a, **k: [])

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "context" / "analytics.yaml").write_text("sessions_tracked: 2\ntotal_learnings: 22\n", encoding="utf-8")

    body = _build_learnings_summary(trw_dir, TRWConfig())

    assert "not measured" in body
    assert "Entries in this project's store: 0" not in body
