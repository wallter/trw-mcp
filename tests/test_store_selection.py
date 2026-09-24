"""``selected_store`` is the one way trw-mcp reaches its memory (PRD-CORE-280 FR01)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import reload_config
from trw_mcp.state import _store_selection
from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

pytestmark = pytest.mark.unit


@pytest.fixture
def trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    reload_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    yield trw_dir
    reload_config()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    return store


def test_an_unmigrated_checkout_fails_closed_naming_the_migration(trw_dir: Path) -> None:
    """Its own memory.db is never read or written again; the error names the one way forward."""
    (trw_dir / "memory").mkdir()
    (trw_dir / "memory" / "memory.db").write_bytes(b"SQLite format 3\x00 rows")

    with pytest.raises(StoreUnavailableError, match="trw-mcp memory migrate --to user"):
        selected_store(trw_dir)

    assert (trw_dir / "memory" / "memory.db").read_bytes() == b"SQLite format 3\x00 rows"


def test_a_checkout_with_nothing_to_migrate_is_told_to_pin_and_mint(trw_dir: Path) -> None:
    """No rows to move (PRD-CORE-280 FR06): update-project pins it and mints its grant."""
    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(trw_dir)

    assert not (trw_dir / "memory").exists()


def test_an_initialized_store_with_no_learnings_is_not_told_to_migrate(trw_dir: Path) -> None:
    """Emptiness is the rows, not the file size: ``holds_rows``, the rule update-project pins by (FR06)."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    SQLiteBackend(trw_dir / "memory" / "memory.db").close()

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(trw_dir)


def test_a_pinned_checkout_gets_the_daemon_store_and_never_opens_the_project_file(
    trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant

    from trw_mcp.state._daemon_store import DaemonMemoryStore

    monkeypatch.setenv("TRW_PROJECT_NAMESPACE", "project:acme-1a2b3c4d")
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    reload_config()

    with pytest.raises(StoreUnavailableError, match="trw-mcp memory token"):
        selected_store(trw_dir)
    write_checkout_grant(trw_dir, mint_grant(DaemonPaths.resolve(), ["project:acme-1a2b3c4d"]))
    store, namespace = selected_store(trw_dir)

    assert (type(store), namespace) == (DaemonMemoryStore, "project:acme-1a2b3c4d")
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_store_learning_writes_through_the_selected_store(trw_dir: Path, fake: FakeMemoryStore) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    result = store_learning(trw_dir, "L-s1", "Routed summary", "detail", impact=0.6, scope="project")

    assert result["status"] == "recorded"
    assert ("put", ("Routed summary", "default")) in fake.calls
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_update_learning_reads_and_writes_through_the_selected_store(trw_dir: Path, fake: FakeMemoryStore) -> None:
    from trw_mcp.state.memory_adapter import update_learning

    fake.put("Original", "default", {"entry_id": "L-u1", "importance": 0.3})

    result = update_learning(trw_dir, "L-u1", impact=0.9)

    assert result["status"] == "updated"
    assert ("correct", ("L-u1", {"impact": 0.9})) in fake.calls
    assert fake.get("L-u1").importance == 0.9  # type: ignore[union-attr]
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_the_pin_is_read_from_this_checkouts_config_not_the_process_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two roots in one process: the singleton's root must not decide the other's store."""
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    pinned, unpinned = tmp_path / "pinned" / ".trw", tmp_path / "unpinned" / ".trw"
    for trw in (pinned, unpinned):
        trw.mkdir(parents=True)
    (pinned / "config.yaml").write_text("project_namespace: project:acme-1a2b3c4d\n", encoding="utf-8")

    for singleton_root in (unpinned, pinned):
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(singleton_root.parent))
        reload_config()
        with pytest.raises(StoreUnavailableError, match="trw-mcp memory token"):
            selected_store(pinned)
        with pytest.raises(StoreUnavailableError, match="trw-mcp update-project"):
            selected_store(unpinned)
    reload_config()


def test_a_user_scope_learning_is_put_into_the_user_namespace(
    trw_dir: Path, fake: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._tier_routing import USER_NAMESPACE
    from trw_mcp.state.memory_adapter import store_learning

    store_learning(trw_dir, "L-user1", "Portable summary", "detail", impact=0.6, scope="user")

    assert ("put", ("Portable summary", USER_NAMESPACE)) in fake.calls


# -- recall (PRD-CORE-280 FR01 slice d) ---------------------------------------------------------


def test_recall_learnings_reads_through_the_selected_store(trw_dir: Path, fake: FakeMemoryStore) -> None:
    from trw_mcp.state.memory_adapter import recall_learnings

    fake.put("Retry the gizmoflux handler", "default", {"entry_id": "L-q1", "importance": 0.8})

    rows = recall_learnings(trw_dir, "gizmoflux", status="active")

    assert [row["id"] for row in rows] == ["L-q1"]
    assert ("recall", ("gizmoflux", (), True)) in fake.calls
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_the_ids_fetch_reads_through_the_selected_store(trw_dir: Path, fake: FakeMemoryStore) -> None:
    from trw_mcp.state._recall_admission import fetch_admitted

    fake.put("Kept", "default", {"entry_id": "L-q2"})

    assert [row.id for row in fetch_admitted(trw_dir, ["L-q2", "L-q2", "L-none"])] == ["L-q2"]
    assert ("recall", ("*", ("L-q2", "L-none"), True)) in fake.calls
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_an_unreachable_store_is_reported_not_read_as_no_learnings(
    trw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state.memory_adapter import recall_learnings

    class _Down(FakeMemoryStore):
        def recall(self, spec: object) -> list[object]:  # type: ignore[override]
            raise StoreUnavailableError("the memory daemon is not running. Run trw-mcp doctor.")

    monkeypatch.setattr(_store_selection, "selected_store", lambda _dir: (_Down(), "project:acme-1a2b3c4d"))

    assert recall_learnings(trw_dir, "anything") == []
    assert "trw-mcp doctor" in (pop_store_error() or "")


def test_a_checkout_with_no_grant_is_reported_by_recall_too(
    trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store selection fails before any read; recall still reports it rather than raising."""
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state.memory_adapter import recall_learnings

    monkeypatch.setenv("TRW_PROJECT_NAMESPACE", "project:acme-1a2b3c4d")
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    reload_config()

    assert recall_learnings(trw_dir, "anything") == []
    assert "trw-mcp memory token" in (pop_store_error() or "")
    assert not (trw_dir / "memory" / "memory.db").exists()


def test_the_recall_extras_read_and_gate_through_the_selected_store(
    trw_dir: Path, fake: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Near-duplicate vectors come from the store; shared results pass the store's gate (PRD-CORE-280 FR01)."""
    from trw_memory.embeddings.provenance import EmbeddingSpace
    from trw_memory.sync import SharedFetchResult

    from trw_mcp.tools import _recall_impl

    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(
        "trw_mcp.state._embedding_space.loaded_embedding_space", lambda: EmbeddingSpace("d" * 64, "enc", 2)
    )
    fake.stored_vectors = {"L-a": [1.0, 0.0], "L-b": [1.0, 0.0]}
    rows: list[dict[str, object]] = [
        {"id": "L-a", "summary": "First wording", "detail": ""},
        {"id": "L-b", "summary": "Second wording", "detail": ""},
    ]

    deduped, _collapsed = _recall_impl._dedup_ranked_learnings(trw_dir, rows)
    assert [row["id"] for row in deduped] == ["L-a"]  # identical vectors, different text
    assert ("vectors", ("L-a", "L-b")) in fake.calls

    def _fetch(query: str, cfg: object, *, admit: object) -> SharedFetchResult:
        outcome = admit([{"id": "R-1", "summary": "[shared] tip"}])  # type: ignore[operator]
        return SharedFetchResult(outcome.admitted, "ok", 1, outcome.refused)

    monkeypatch.setattr("trw_memory.sync.fetch_shared_memories", _fetch)
    merged, _status = _recall_impl._augment_with_remote("q", rows)
    assert [row["id"] for row in merged] == ["L-a", "L-b", "R-1"]
    assert ("admit_shared", 1) in fake.calls
    assert not (trw_dir / "memory" / "memory.db").exists()
