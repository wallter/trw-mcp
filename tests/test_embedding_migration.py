"""Automatic embedding migration: bounded, resumable, offline-aware, one process at a time.

Every test uses a fake two-dimensional embedder (no model load, no download)
over a real SQLite + sqlite-vec store, so the vectors, their recorded
provenance and the persisted progress are the production artefacts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from trw_memory.embeddings._hf_cache import CacheProbe, CacheState
from trw_memory.embeddings.provenance import EmbeddingSpace
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._embedding_space_support import NEW_SPACE, OLD_SPACE
from tests._layout import requires_local_timing
from trw_mcp.state import _embedding_migration as migration
from trw_mcp.state import _embedding_migration_schedule as schedule
from trw_mcp.state._embedding_migration import Budget, load_migration_state, run_migration

IDENTITY = {"model": "BAAI/bge-small-en-v1.5", "revision": "rev1", "dim": 2}


class FakeEmbedder:
    """Deterministic 2-d encoder in *space*; counts every document encode."""

    def __init__(self, space: EmbeddingSpace | None = NEW_SPACE, *, fail: bool = False) -> None:
        self.space = space
        self.fail = fail
        self.inputs: list[str] = []
        self.gate: threading.Event | None = None

    def embedding_space(self) -> EmbeddingSpace | None:
        return self.space

    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float] | None:
        if self.gate is not None:
            self.gate.wait(5)
        self.inputs.append(text)
        if self.fail:
            raise RuntimeError("encoder failed")
        return [1.0, float(len(text) % 7) / 7.0]

    def embed_query(self, text: str) -> list[float] | None:
        return self.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        return [self.embed(text) for text in texts]

    @property
    def dim(self) -> int:
        return 2


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project store holding 7 learnings whose vectors predate the active space."""
    pytest.importorskip("sqlite_vec")
    trw = tmp_path / ".trw"
    db = trw / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    backend = SQLiteBackend(db, dim=2)
    assert backend.vec_available
    for i in range(7):
        entry = MemoryEntry(id=f"L-{i}", content=f"learning number {i}", detail="detail")
        backend.store(entry)
        # Four legacy vectors with no recorded space, three from the old model.
        if i < 4:
            backend.upsert_vector(entry.id, [0.0, 1.0], namespace="default")
        else:
            from trw_memory.embeddings.provenance import VectorProvenance

            text = f"{entry.content} {entry.detail}"
            proof = VectorProvenance.for_vector(OLD_SPACE, text, [0.0, 1.0])
            backend.upsert_vector(entry.id, [0.0, 1.0], namespace="default", provenance=proof)
    backend.close()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    return trw


@pytest.fixture
def two_dim_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the fake 2-d encoder as the retrieval model (the plan reads config)."""
    from trw_mcp.models.config import TRWConfig

    config = TRWConfig(retrieval_embedding_model=str(IDENTITY["model"]), retrieval_embedding_dim=2)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)


def _records(trw: Path) -> dict[str, object]:
    backend = SQLiteBackend(trw / "memory" / "memory.db", dim=2)
    try:
        return backend.get_vector_records([f"L-{i}" for i in range(7)], namespace="default")
    finally:
        backend.close()


def _in_new_space(trw: Path) -> int:
    return sum(1 for r in _records(trw).values() if r.provenance is not None and r.provenance.space == NEW_SPACE)


def test_bounded_runs_resume_until_every_stale_vector_is_migrated(store: Path) -> None:
    embedder = FakeEmbedder()
    budget = Budget(seconds=60, rows=3, page_size=2)

    first = run_migration(store, embedder, identity=IDENTITY, budget=budget)
    assert first["run"] == "worked" and first["status"] == "in_progress"
    assert first["total"] == 7 and first["repaired"] == 3
    assert _in_new_space(store) == 3
    persisted = load_migration_state(store)
    assert persisted is not None and persisted.cursor is not None and persisted.repaired == 3

    # A fresh "process" (new embedder object) resumes at the persisted cursor:
    # the rows already migrated are never encoded again.
    resumed = FakeEmbedder()
    second = run_migration(store, resumed, identity=IDENTITY, budget=budget)
    third = run_migration(store, resumed, identity=IDENTITY, budget=budget)
    assert second["repaired"] == 6 and third["status"] == "complete" and third["repaired"] == 7
    assert len(embedder.inputs) + len(resumed.inputs) == 7
    assert _in_new_space(store) == 7

    # Idempotent once complete: nothing measured stale, nothing encoded.
    again = FakeEmbedder()
    assert run_migration(store, again, identity=IDENTITY, budget=budget)["run"] == "idle"
    assert again.inputs == []


def test_time_budget_stops_a_run_between_pages(store: Path) -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    budget = Budget(seconds=1.0, rows=1000, page_size=2, clock=lambda: next(ticks))
    report = run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=budget)
    assert report["repaired"] == 2 and report["status"] == "in_progress"


def test_completion_restarts_the_graph_sweep(store: Path) -> None:
    sweep = store / "memory" / "graph-backfill.json"
    sweep.write_text(
        json.dumps({"version": 1, "namespaces": {"default": {"updated_at": "x", "entry_id": "y", "complete": True}}})
    )
    run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    state = json.loads(sweep.read_text())["namespaces"]["default"]
    assert state == {"updated_at": None, "entry_id": None, "complete": False}


def test_a_model_change_restarts_the_migration(store: Path) -> None:
    run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    newer = EmbeddingSpace("c" * 64, "trw-declared-encoder-v1:next-model", 2)
    report = run_migration(store, FakeEmbedder(newer), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    assert report["status"] == "complete" and report["total"] == 7 and report["repaired"] == 7


def test_rows_that_keep_failing_stall_instead_of_retrying_forever(store: Path) -> None:
    broken = FakeEmbedder(fail=True)
    report = run_migration(store, broken, identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    assert report["status"] == "stalled" and report["repaired"] == 0 and report["residual"] == 7
    assert len(broken.inputs) == 7
    # A stalled store is not re-attempted until its stale count changes.
    retry = FakeEmbedder()
    assert run_migration(store, retry, identity=IDENTITY, budget=Budget(seconds=60, rows=1000))["run"] == "idle"
    assert retry.inputs == []


def test_a_held_lock_makes_another_process_report_busy_and_do_nothing(store: Path) -> None:
    lock = migration.migration_lock_path(store)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys\n"
            f"f = open({str(lock)!r}, 'a+')\n"
            "fcntl.flock(f, fcntl.LOCK_EX)\n"
            "print('locked', flush=True)\n"
            "sys.stdin.readline()\n",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        embedder = FakeEmbedder()
        report = run_migration(store, embedder, identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
        assert report["run"] == "busy" and embedder.inputs == []
        assert _in_new_space(store) == 0
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(10)
    assert (
        run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))["status"]
        == "complete"
    )


def test_concurrent_runs_encode_each_row_once(store: Path) -> None:
    embedder = FakeEmbedder()
    embedder.gate = threading.Event()  # the winner encodes (holding the lock) only once released
    reports: list[dict[str, object]] = []
    returned = threading.Condition()

    def worker() -> None:
        report = run_migration(store, embedder, identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
        with returned:
            reports.append(report)
            returned.notify_all()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    with returned:  # both losers return while the winner is still holding the lock
        assert returned.wait_for(lambda: len(reports) == 2, timeout=10)
    embedder.gate.set()
    for thread in threads:
        thread.join(10)
    assert sorted(str(r["run"]) for r in reports) == ["busy", "busy", "worked"]
    assert len(embedder.inputs) == 7 and _in_new_space(store) == 7
    state = load_migration_state(store)
    assert state is not None and state.status == "complete" and state.repaired == 7


def test_session_plan_skips_offline_when_the_model_is_not_cached(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_OFFLINE", "1")
    monkeypatch.setattr(
        "trw_memory.embeddings._hf_cache.probe_model_cache", lambda _model: CacheProbe(CacheState.ABSENT)
    )
    monkeypatch.setattr(schedule, "schedule_embedding_migration", lambda *a: pytest.fail("migration started"))
    line = schedule.plan_session_migration(store)
    assert line is not None and line["status"] == "skipped_offline"
    assert "4 stored vectors" in str(line["message"]) and "TRW_OFFLINE" in str(line["message"])


def test_session_plan_runs_offline_from_a_complete_cache(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(
        "trw_memory.embeddings._hf_cache.probe_model_cache",
        lambda _model: CacheProbe(CacheState.COMPLETE, snapshot_path="/cache/snapshots/rev1"),
    )
    started: list[object] = []
    monkeypatch.setattr(schedule, "schedule_embedding_migration", lambda *a: started.append(a) or True)
    line = schedule.plan_session_migration(store)
    assert started and line is not None and line["status"] == "scheduled"


@pytest.mark.usefixtures("two_dim_config")
@requires_local_timing
def test_session_start_is_not_blocked_by_the_model_load(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan returns at once; the load and the work happen on the background thread."""
    from trw_mcp.state import _memory_connection as conn

    embedder = FakeEmbedder()
    loaded = threading.Event()

    def slow_get_embedder() -> FakeEmbedder:
        loaded.wait(10)
        return embedder

    monkeypatch.setattr(conn, "get_embedder", slow_get_embedder)
    monkeypatch.setattr(schedule, "encoder_identity", lambda: dict(IDENTITY))
    monkeypatch.setattr(schedule, "offline_block", lambda _model: "")  # the suite runs with TRW_OFFLINE=1
    started = time.monotonic()
    line = schedule.plan_session_migration(store)
    assert time.monotonic() - started < 2.0
    assert line == {
        "status": "scheduled",
        "message": (
            "re-embedding 0/4 stored vectors to BAAI/bge-small-en-v1.5 in the background; "
            "dense recall and similarity links are partial until done"
        ),
    }
    # A second session in the same process does not start a second thread.
    assert schedule.schedule_embedding_migration(store) is False
    loaded.set()
    schedule.wait_for_migration(10)
    assert _in_new_space(store) == 7
    # Finished for this model and revision, with no new space-less vectors:
    # the next session reports nothing, and its background check exits
    # before loading a model.
    loads: list[str] = []
    monkeypatch.setattr(conn, "get_embedder", lambda: loads.append("load"))
    assert schedule.migration_needed(store, dict(IDENTITY)) is False
    assert schedule.plan_session_migration(store) is None
    schedule.wait_for_migration(10)
    assert loads == []


@pytest.mark.usefixtures("two_dim_config")
def test_in_progress_state_is_reported_with_its_progress(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=3, page_size=3))
    monkeypatch.setattr(schedule, "encoder_identity", lambda: dict(IDENTITY))
    monkeypatch.setattr(schedule, "offline_block", lambda _model: "")
    monkeypatch.setattr(schedule, "schedule_embedding_migration", lambda *a: True)
    line = schedule.plan_session_migration(store)
    assert line is not None and line["status"] == "in_progress"
    assert str(line["message"]).startswith("re-embedding 3/7 stored vectors")


def test_a_new_space_less_vector_reopens_a_finished_migration(store: Path) -> None:
    run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    assert schedule.migration_needed(store, dict(IDENTITY)) is False
    assert schedule.migration_needed(store, {**IDENTITY, "revision": "rev2"}) is True
    backend = SQLiteBackend(store / "memory" / "memory.db", dim=2)
    try:
        backend.upsert_vector("L-0", [0.0, 1.0], namespace="default")
    finally:
        backend.close()
    assert schedule.migration_needed(store, dict(IDENTITY)) is True
    report = run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    assert report["status"] == "complete" and report["repaired"] == 8 and _in_new_space(store) == 7


def test_session_maintenance_surfaces_the_migration_line(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.memory_pressure import WriterCensus
    from trw_mcp.tools._ceremony_embeddings_maintenance import run_embeddings_maintenance

    line = {"status": "scheduled", "message": "re-embedding 0/4 stored vectors"}
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.check_embeddings_status",
        lambda **_kw: {"enabled": True, "available": True, "advisory": ""},
    )
    monkeypatch.setattr(schedule, "plan_session_migration", lambda trw_dir: line)
    census = WriterCensus(
        writer_pids=(),
        writer_count=0,
        peer_writer_count=0,
        threshold=3,
        under_pressure=False,
        census_state="measured",
        identity_state="verified",
        heartbeat_state="measured",
    )
    maintenance: dict[str, object] = {}
    run_embeddings_maintenance(store, TRWConfig(), maintenance, census=census, defer_memory_heavy=False)  # type: ignore[arg-type]
    assert maintenance["embeddings_migration"] == line
    maintenance.clear()
    config = TRWConfig(embeddings_auto_backfill_on_low_coverage=False)
    run_embeddings_maintenance(store, config, maintenance, census=census, defer_memory_heavy=False)  # type: ignore[arg-type]
    assert "embeddings_migration" not in maintenance


def test_update_project_reports_the_foreground_migration(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._update_external import _migrate_stale_vectors
    from trw_mcp.state import _memory_connection as conn

    monkeypatch.setattr(conn, "get_embedder", FakeEmbedder)
    monkeypatch.setattr(schedule, "encoder_identity", lambda: dict(IDENTITY))
    result: dict[str, list[str]] = {"warnings": []}
    _migrate_stale_vectors(store, result, 120, None)
    assert result["info"] == ["Re-embedded 7/7 stored vectors to BAAI/bge-small-en-v1.5 (complete)"]
    assert result["warnings"] == [] and _in_new_space(store) == 7


def test_a_store_without_vectors_never_loads_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new project (no store, or a store with no vectors) has nothing to migrate.

    Regression: with a warm HF cache the background thread used to load the
    model at every session start of a vector-less project, for no work.
    """
    monkeypatch.setattr(schedule, "schedule_embedding_migration", lambda *a: pytest.fail("migration started"))
    trw = tmp_path / ".trw"
    assert schedule.vector_counts(trw) == (0, 0)
    assert schedule.plan_session_migration(trw) is None
    pytest.importorskip("sqlite_vec")
    (trw / "memory").mkdir(parents=True)
    backend = SQLiteBackend(trw / "memory" / "memory.db", dim=2)
    try:
        backend.store(MemoryEntry(id="L-0", content="no vector yet"))
    finally:
        backend.close()
    assert schedule.vector_counts(trw) == (0, 0)
    assert schedule.plan_session_migration(trw) is None


@pytest.mark.usefixtures("two_dim_config")
def test_a_stalled_migration_is_reported_and_not_rescheduled(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_migration(store, FakeEmbedder(fail=True), identity=IDENTITY, budget=Budget(seconds=60, rows=1000))
    monkeypatch.setattr(schedule, "schedule_embedding_migration", lambda *a: pytest.fail("stalled store rescheduled"))
    line = schedule.plan_session_migration(store)
    assert line is not None and line["status"] == "stalled"
    assert str(line["message"]).startswith("7 stored vectors could not be re-embedded to BAAI/bge-small-en-v1.5")


def test_changed_canonical_row_retried_on_next_bounded_run(store: Path) -> None:
    other = SQLiteBackend(store / "memory" / "memory.db", dim=2)

    class RacingEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float] | None:
            if not self.inputs:
                entry = other.list_entries(namespace="default", limit=1)[0]
                assert entry is not None
                entry.content = "concurrently edited learning"
                other.store(entry)
            return super().embed(text)

    try:
        first = run_migration(
            store, RacingEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=1, page_size=1)
        )
        assert first["status"] == "in_progress"
        persisted = load_migration_state(store)
        assert persisted is not None and persisted.pass_changed == 1
        assert _in_new_space(store) == 0
        continued = run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=6))
        assert continued["status"] == "in_progress"
        assert _in_new_space(store) == 6
        second = run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=7))
        assert second["status"] == "complete"
        assert _in_new_space(store) == 7
        assert (
            run_migration(store, FakeEmbedder(), identity=IDENTITY, budget=Budget(seconds=60, rows=7))["run"] == "idle"
        )
    finally:
        other.close()
