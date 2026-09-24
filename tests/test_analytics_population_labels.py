"""PRD-FIX-141-FR04 — every generated count names the population it counts.

Three surfaces printed a learning count on 2026-09-16 and none of them said what
it counted: the generated ``.trw/INSTRUCTIONS.md`` said ``0 learnings from 0
prior sessions``, ``trw://learnings/summary`` said ``Total learnings: 9``, and
the store held 1,346 entries (learning L-Rikf). The zero was the worst of the
three, because that file is ``@``-included into every session: it told every
agent the corpus was empty.

These tests drive the real generators over the store's health block (the fake
store; canary exclusion and namespace scoping are the store's own, tested in
trw-memory's ``test_tools_status_health``). The negative case — an unmeasured
store must render "not measured", never "0" — is the one that would have caught
the original defect.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state import _store_selection
from trw_mcp.state._store_counts import StoreCounts, read_store_counts


def _write_store(store: FakeMemoryStore, *, local: int, synced: int) -> None:
    """Stock the pinned namespace with the requested provenance mix."""
    for i in range(local):
        store.rows[(FAKE_NAMESPACE, f"local{i}")] = MemoryEntry(id=f"local{i}", content="x", namespace=FAKE_NAMESPACE)
    for i in range(synced):
        store.rows[(FAKE_NAMESPACE, f"sync{i}")] = MemoryEntry(
            id=f"sync{i}", content="x", namespace=FAKE_NAMESPACE, source="team_sync"
        )


# ---------------------------------------------------------------------------
# The one reader
# ---------------------------------------------------------------------------


def test_store_counts_split_local_from_synced(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """The split is the whole point: one total for two populations is the defect."""
    trw_dir = tmp_path / ".trw"
    _write_store(fake_memory_store, local=22, synced=1324)

    counts = read_store_counts(trw_dir)

    assert counts == StoreCounts(total=1346, local=22, synced=1324)


def _unpinned(_trw_dir: Path) -> None:
    raise _store_selection.StoreUnavailableError("no project_namespace; run `trw-mcp update-project`")


def _unreachable(_namespace: str) -> None:
    raise _store_selection.StoreUnavailableError("the memory daemon is unreachable")


@pytest.mark.parametrize("unmeasured", ["unpinned-checkout", "unreachable-daemon"])
def test_an_unmeasurable_store_is_none_and_never_zero(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, unmeasured: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``None`` and ``StoreCounts(0, 0, 0)`` are different answers, deliberately."""
    if unmeasured == "unpinned-checkout":
        monkeypatch.setattr(_store_selection, "selected_store", _unpinned)
    else:
        monkeypatch.setattr(fake_memory_store, "health", _unreachable)

    assert read_store_counts(tmp_path / ".trw") is None


def test_an_unmigrated_memory_db_is_not_this_checkouts_inventory(tmp_path: Path) -> None:
    """An unpinned checkout's memory.db holds rows the daemon never serves: not measured (PRD-CORE-280)."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = SQLiteBackend(trw_dir / "memory" / "memory.db")
    backend.store(MemoryEntry(id="L-1", content="an unmigrated learning"))
    backend.close()
    before = (trw_dir / "memory" / "memory.db").read_bytes()

    def refuse_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the store count opened a checkout's memory.db")

    with patch("sqlite3.connect", side_effect=refuse_open):
        assert read_store_counts(trw_dir) is None
    assert (trw_dir / "memory" / "memory.db").read_bytes() == before


def test_a_genuinely_empty_store_counts_zero_not_none(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """The other half of the distinction: a read store with no rows IS zero."""
    trw_dir = tmp_path / ".trw"
    _write_store(fake_memory_store, local=0, synced=0)

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


def test_the_store_count_is_cached_within_a_turn(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bounded query per turn: the instruction surface renders several sections."""
    from trw_mcp.state.claude_md.sections import _memory_routing as routing

    trw_dir = tmp_path / ".trw"
    _write_store(fake_memory_store, local=3, synced=0)
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
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    _write_store(fake_memory_store, local=22, synced=1324)
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
