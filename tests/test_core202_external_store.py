"""PRD-CORE-202: MCP consumption path for a pre-built external/consolidated corpus DB.

``extra_read_stores`` (config) / ``--memory-db`` (startup flag) register one or
more external trw-memory SQLite DBs as READ-ONLY sources unioned into
``trw_recall``. With none configured, recall is byte-identical to HEAD
(project ∪ user tier only). External corpora are never written; a missing or
schema-incompatible store is skipped with a structured warning (fail-open).

FRs covered:
- FR01: ``extra_read_stores`` config field (default ``[]``; list parse; type guard).
- FR02: resolve + attach N distinct read-only backends; empty -> none; de-dup by resolved path.
- FR03: ``--memory-db`` flag (``action="append"``) -> union with config.
- FR04: read-only + graceful degradation (missing/schema-incompatible -> skipped, logged).
- FR05: e2e distill-corpus stand-in surfaces via recall only when registered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate the user tier + reset all backend singletons each test."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("TRW_USER_TIER_ENABLED", raising=False)
    monkeypatch.delenv("TRW_EXTRA_READ_STORES", raising=False)
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    from trw_mcp.state import _external_store

    _external_store.reset_external_backends()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    from trw_mcp.state import _external_store

    _external_store.reset_external_backends()
    _reset_config()


def _trw_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(r.get("id")) for r in rows]


def _seed_external_db(db_path: Path, ids_and_text: list[tuple[str, str]]) -> None:
    """Write a tiny consolidated-corpus stand-in DB at *db_path* (FR05)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(db_path, dim=384)
    try:
        for entry_id, text in ids_and_text:
            backend.store(
                MemoryEntry(
                    id=entry_id,
                    content=text,
                    detail=f"detail for {entry_id}",
                    importance=0.6,
                    namespace="default",
                    source="consolidated",
                )
            )
    finally:
        backend.close()


# --------------------------------------------------------------------------- FR01


def test_config_field_default_is_empty_list() -> None:
    cfg = TRWConfig()
    assert cfg.extra_read_stores == []


def test_config_field_parses_path_list() -> None:
    cfg = TRWConfig(extra_read_stores=["/abs/corpus/default/memory.db"])
    assert cfg.extra_read_stores == [Path("/abs/corpus/default/memory.db")]


def test_config_field_rejects_non_list_scalar() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TRWConfig(extra_read_stores=123)  # type: ignore[arg-type]


def test_external_store_recall_cap_default_and_floor() -> None:
    from pydantic import ValidationError

    assert TRWConfig().external_store_recall_cap == 5
    with pytest.raises(ValidationError):
        TRWConfig(external_store_recall_cap=0)


# --------------------------------------------------------------------------- FR02


def test_resolve_paths_empty_when_unset() -> None:
    from trw_mcp.state import _external_store

    assert _external_store.resolve_external_store_paths(TRWConfig()) == []


def test_resolve_paths_dedup_by_resolved_path(tmp_path: Path) -> None:
    from trw_mcp.state import _external_store

    db = tmp_path / "corpus" / "memory.db"
    cfg = TRWConfig(extra_read_stores=[str(db), str(db)])
    resolved = _external_store.resolve_external_store_paths(cfg)
    assert resolved == [db.resolve()]


def test_resolve_paths_excludes_default_store(tmp_path: Path) -> None:
    """RISK-02: pointing an external path at the project's own store is dropped."""
    from trw_mcp.state import _external_store

    default_db = tmp_path / "proj" / ".trw" / "memory" / "memory.db"
    cfg = TRWConfig(extra_read_stores=[str(default_db)])
    resolved = _external_store.resolve_external_store_paths(cfg, default_db_path=default_db)
    assert resolved == []


def test_get_external_backends_one_per_path(tmp_path: Path) -> None:
    from trw_mcp.state import _external_store

    db1 = tmp_path / "c1" / "memory.db"
    db2 = tmp_path / "c2" / "memory.db"
    _seed_external_db(db1, [("L-c1", "alpha record one")])
    _seed_external_db(db2, [("L-c2", "beta record two")])
    cfg = TRWConfig(extra_read_stores=[str(db1), str(db2)])
    backends = _external_store.get_external_backends(cfg)
    assert len(backends) == 2
    # Distinct files.
    assert {Path(b._db_path).resolve() for b in backends} == {db1.resolve(), db2.resolve()}


def test_get_external_backends_empty_when_unset() -> None:
    from trw_mcp.state import _external_store

    assert _external_store.get_external_backends(TRWConfig()) == []


def test_external_backends_are_singletons(tmp_path: Path) -> None:
    from trw_mcp.state import _external_store

    db = tmp_path / "c" / "memory.db"
    _seed_external_db(db, [("L-x", "gamma record")])
    cfg = TRWConfig(extra_read_stores=[str(db)])
    first = _external_store.get_external_backends(cfg)
    second = _external_store.get_external_backends(cfg)
    assert first[0] is second[0]


# --------------------------------------------------------------------------- FR03


def test_memory_db_flag_action_append() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--memory-db", "/abs/a.db", "--memory-db", "/abs/b.db", "serve"])
    assert args.memory_db == ["/abs/a.db", "/abs/b.db"]


def test_memory_db_flag_default_none() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["serve"])
    assert args.memory_db is None


def test_cli_paths_union_with_config(tmp_path: Path) -> None:
    """--memory-db paths union with config.extra_read_stores, de-duped by resolved path."""
    from trw_mcp.state import _external_store

    a = tmp_path / "a" / "memory.db"
    b = tmp_path / "b" / "memory.db"
    cfg = TRWConfig(extra_read_stores=[str(a)])
    _external_store.register_cli_memory_db_paths([str(a), str(b)])
    resolved = _external_store.resolve_external_store_paths(cfg)
    assert resolved == [a.resolve(), b.resolve()]


def test_cli_paths_none_is_noop() -> None:
    from trw_mcp.state import _external_store

    _external_store.register_cli_memory_db_paths(None)
    assert _external_store.resolve_external_store_paths(TRWConfig()) == []


# --------------------------------------------------------------------------- FR03 wiring
# delivered != wired: the prior commit added register_cli_memory_db_paths and the
# --memory-db flag, but the serve-dispatch path (server/_cli.py::main) must actually
# CALL the registration or the parsed flag is inert. These tests pin the wiring.


def _serve_namespace(memory_db: list[str] | None) -> object:
    """A minimal argparse-like namespace for the serve (default) dispatch path."""
    import argparse

    return argparse.Namespace(
        command="serve",
        memory_db=memory_db,
        debug=False,
        verbose=0,
        quiet=False,
        log_level=None,
        log_json=None,
        allow_unsigned=None,
        transport="stdio",
        host=None,
        port=None,
    )


def test_serve_dispatch_registers_memory_db_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() on the serve path MUST call register_cli_memory_db_paths with args.memory_db.

    Without the wiring in _cli.py this fails: _cli_memory_db_paths stays empty even
    though --memory-db was parsed, so the external store never federates into recall.
    """
    from trw_mcp.server import _cli
    from trw_mcp.state import _external_store

    db = tmp_path / "corpus" / "memory.db"
    _seed_external_db(db, [("L-wired", "wired corpus record")])
    namespace = _serve_namespace([str(db)])

    captured: list[list[str] | None] = []
    real_register = _external_store.register_cli_memory_db_paths

    def _spy(paths: list[str] | None) -> None:
        captured.append(paths)
        real_register(paths)

    monkeypatch.setattr(_external_store, "register_cli_memory_db_paths", _spy)

    parser = _cli._build_arg_parser()
    monkeypatch.setattr(parser, "parse_args", lambda: namespace)
    monkeypatch.setattr(_cli, "_build_arg_parser", lambda: parser)
    # Neutralize the heavy startup paths so main() returns after dispatch wiring.
    monkeypatch.setattr(_cli, "_boot_sequence", lambda *a, **k: None)
    monkeypatch.setattr(_cli, "_check_mcp_json_portability", lambda *a, **k: None)
    monkeypatch.setattr("trw_mcp.server._transport.resolve_and_run_transport", lambda *a, **k: None)

    _cli.main()

    assert captured == [[str(db)]], "serve dispatch must register args.memory_db (FR03 wiring)"
    # And the registration must take effect: the resolved external set includes the db.
    assert db.resolve() in _external_store.resolve_external_store_paths(TRWConfig())


def test_serve_dispatch_no_memory_db_flag_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No --memory-db => no external path registered (NFR01 hot-path no-op)."""
    from trw_mcp.server import _cli
    from trw_mcp.state import _external_store

    namespace = _serve_namespace(None)
    captured: list[list[str] | None] = []
    real_register = _external_store.register_cli_memory_db_paths

    def _spy(paths: list[str] | None) -> None:
        captured.append(paths)
        real_register(paths)

    monkeypatch.setattr(_external_store, "register_cli_memory_db_paths", _spy)
    parser = _cli._build_arg_parser()
    monkeypatch.setattr(parser, "parse_args", lambda: namespace)
    monkeypatch.setattr(_cli, "_build_arg_parser", lambda: parser)
    monkeypatch.setattr(_cli, "_boot_sequence", lambda *a, **k: None)
    monkeypatch.setattr(_cli, "_check_mcp_json_portability", lambda *a, **k: None)
    monkeypatch.setattr("trw_mcp.server._transport.resolve_and_run_transport", lambda *a, **k: None)

    _cli.main()

    assert captured == [None]
    assert _external_store.resolve_external_store_paths(TRWConfig()) == []


def test_memory_db_flag_record_surfaces_via_recall_after_serve_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end FR03: a record in the --memory-db DB surfaces via trw_recall (union).

    Drives the REAL serve-dispatch wiring (main()) with --memory-db, then proves a
    record in that external DB is returned by recall_learnings in a project whose own
    store holds only unrelated records — the exact reproduced dead-end, now closed.
    """
    from trw_mcp.server import _cli
    from trw_mcp.state import _external_store

    repo = _trw_dir(tmp_path, "proj")
    memory_adapter.store_learning(repo, "L-projX", "unrelated local note about ledgers", "x")

    corpus = tmp_path / "estate" / "default" / "memory.db"
    _seed_external_db(
        corpus,
        [("L-estate1", "consolidated estate knowledge about nebula provisioning")],
    )

    namespace = _serve_namespace([str(corpus)])
    parser = _cli._build_arg_parser()
    monkeypatch.setattr(parser, "parse_args", lambda: namespace)
    monkeypatch.setattr(_cli, "_build_arg_parser", lambda: parser)
    monkeypatch.setattr(_cli, "_boot_sequence", lambda *a, **k: None)
    monkeypatch.setattr(_cli, "_check_mcp_json_portability", lambda *a, **k: None)
    monkeypatch.setattr("trw_mcp.server._transport.resolve_and_run_transport", lambda *a, **k: None)

    # Before serve wiring runs, the corpus path is NOT registered.
    assert _external_store.resolve_external_store_paths(TRWConfig()) == []

    _cli.main()

    # After serve wiring, the flag is registered and the corpus federates into recall.
    assert corpus.resolve() in _external_store.resolve_external_store_paths(TRWConfig())
    rows = memory_adapter.recall_learnings(repo, "nebula provisioning knowledge", max_results=10)
    ids = _ids(rows)
    assert "L-estate1" in ids, f"--memory-db corpus must surface via recall (got {ids})"


# --------------------------------------------------------------------------- FR04


def test_missing_external_store_skipped_logged(tmp_path: Path) -> None:
    from trw_mcp.state import _external_store

    missing = tmp_path / "nope" / "memory.db"
    cfg = TRWConfig(extra_read_stores=[str(missing)])
    with capture_logs() as logs:
        backends = _external_store.get_external_backends(cfg)
    assert backends == []
    skipped = [e for e in logs if e["event"] == "external_store_skipped"]
    assert skipped and skipped[0]["reason"] == "missing"
    assert Path(str(skipped[0]["path"])) == missing.resolve()


def test_schema_incompatible_store_skipped_logged(tmp_path: Path) -> None:
    """A SQLite DB without a ``memories`` table is excluded (reason=schema_incompatible)."""
    import sqlite3

    from trw_mcp.state import _external_store

    bad = tmp_path / "bad" / "memory.db"
    bad.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(bad))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    cfg = TRWConfig(extra_read_stores=[str(bad)])
    with capture_logs() as logs:
        backends = _external_store.get_external_backends(cfg)
    assert backends == []
    skipped = [e for e in logs if e["event"] == "external_store_skipped"]
    assert skipped and skipped[0]["reason"] == "schema_incompatible"


def test_non_regular_file_skipped(tmp_path: Path) -> None:
    """NFR02: a path that is a directory (not a regular file) is skipped."""
    from trw_mcp.state import _external_store

    d = tmp_path / "adir"
    d.mkdir()
    cfg = TRWConfig(extra_read_stores=[str(d)])
    backends = _external_store.get_external_backends(cfg)
    assert backends == []


def test_write_path_rejects_external_backend(tmp_path: Path) -> None:
    """FR04/NFR02: the write-target guard rejects an external backend."""
    from trw_mcp.state import _external_store

    db = tmp_path / "c" / "memory.db"
    _seed_external_db(db, [("L-ro", "read only record")])
    cfg = TRWConfig(extra_read_stores=[str(db)])
    ext = _external_store.get_external_backends(cfg)[0]
    assert _external_store.is_external_backend(ext) is True
    with pytest.raises(PermissionError):
        _external_store.assert_writable_backend(ext)


def test_federation_fail_open_on_broken_store(tmp_path: Path) -> None:
    """NFR03: a broken external store never raises into recall; project hits survive."""
    from trw_mcp.state import _external_store

    repo = _trw_dir(tmp_path, "repo")
    memory_adapter.store_learning(repo, "L-proj", "project answer about turbo encabulator", "p")
    missing = tmp_path / "gone" / "memory.db"
    cfg = TRWConfig(extra_read_stores=[str(missing)])
    project_entries = [
        e for e in memory_adapter.get_backend(repo).list_entries(namespace="default") if e.id == "L-proj"
    ]
    merged = _external_store.federate_external_stores(project_entries, "turbo encabulator", config=cfg)
    # Fail-open: a missing external store adds nothing; the project hit survives.
    assert [e.id for e in merged] == ["L-proj"]


# --------------------------------------------------------------------------- FR05 (e2e)


@pytest.mark.integration
def test_distill_corpus_surfaces_via_recall_when_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Records from a registered external corpus surface through trw_recall (union)."""
    repo = _trw_dir(tmp_path, "proj")
    # Project store: unrelated records.
    memory_adapter.store_learning(repo, "L-proj1", "unrelated project chore about widgets", "x")
    # External corpus: distinct records on a recognizable topic.
    corpus = tmp_path / "corpus" / "default" / "memory.db"
    _seed_external_db(
        corpus,
        [
            ("L-corpusA", "consolidated estate knowledge about quasar deployment"),
            ("L-corpusB", "consolidated estate knowledge about quasar rollback"),
        ],
    )
    monkeypatch.setenv("TRW_EXTRA_READ_STORES", f'["{corpus}"]')
    _reset_config()

    rows = memory_adapter.recall_learnings(repo, "quasar deployment knowledge", max_results=10)
    ids = _ids(rows)
    assert any(i.startswith("L-corpus") for i in ids), f"external corpus must surface (got {ids})"


@pytest.mark.integration
def test_isolation_when_not_registered(tmp_path: Path) -> None:
    """The reproduced symptom: an external corpus is NOT surfaced when unregistered."""
    repo = _trw_dir(tmp_path, "proj")
    memory_adapter.store_learning(repo, "L-proj1", "unrelated project chore", "x")
    corpus = tmp_path / "corpus" / "default" / "memory.db"
    _seed_external_db(corpus, [("L-corpusA", "consolidated estate knowledge about quasar")])
    # No extra_read_stores configured.
    rows = memory_adapter.recall_learnings(repo, "quasar knowledge", max_results=10)
    ids = _ids(rows)
    assert not any(i.startswith("L-corpus") for i in ids), f"unregistered corpus must NOT leak (got {ids})"


@pytest.mark.integration
def test_union_not_replacement_on_overlapping_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Project AND external records both appear on overlapping query terms (union)."""
    repo = _trw_dir(tmp_path, "proj")
    memory_adapter.store_learning(repo, "L-proj1", "pulsar calibration project note", "p")
    corpus = tmp_path / "corpus" / "default" / "memory.db"
    _seed_external_db(corpus, [("L-corpusA", "pulsar calibration consolidated estate record")])
    monkeypatch.setenv("TRW_EXTRA_READ_STORES", f'["{corpus}"]')
    _reset_config()

    rows = memory_adapter.recall_learnings(repo, "pulsar calibration", max_results=10)
    ids = _ids(rows)
    assert "L-proj1" in ids
    assert "L-corpusA" in ids


# --------------------------------------------------------------------------- NFR02
# Content immutability: a true SQLite ``mode=ro`` open of the external corpus is an
# UPSTREAM trw-memory change (out of scope here) — ``_create_backend`` opens the file
# read-WRITE, so open-time *metadata* (WAL files, chmod) may touch the file. These
# tests prove the stronger property the operator actually cares about: the corpus's
# CONTENT — the ``memories`` table rows — is NEVER mutated across a federation/recall
# cycle. We snapshot the rows + a content hash before, run recall unioning the corpus,
# then assert the content rows are byte-identical after.


def _snapshot_memories_rows(db_path: Path) -> list[tuple[object, ...]]:
    """Return all ``memories`` rows (column-ordered, sorted by id) via a mode=ro open.

    Reads through an independent read-only SQLite connection so the snapshot itself
    can never mutate the corpus. Returns every column so the assertion catches a
    mutation to ANY field (content, access_count, last_accessed_at, recall_count, ...).
    """
    import sqlite3

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
        order = "id" if "id" in cols else cols[0]
        return list(conn.execute(f"SELECT * FROM memories ORDER BY {order}").fetchall())
    finally:
        conn.close()


def _content_hash(rows: list[tuple[object, ...]]) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(repr(rows).encode("utf-8"))
    return h.hexdigest()


def test_external_corpus_content_immutable_across_recall_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: the external corpus ``memories`` rows are byte-identical after recall.

    Even though ``_create_backend`` opens the corpus read-write (open-time WAL/chmod
    may touch the file), no write/prune/access-count mutation reaches the corpus
    CONTENT. Snapshot rows + content hash before; run a real federated recall that
    unions the corpus; assert the rows and hash are unchanged.
    """
    repo = _trw_dir(tmp_path, "proj")
    memory_adapter.store_learning(repo, "L-proj1", "unrelated local widget chore", "x")
    corpus = tmp_path / "estate" / "default" / "memory.db"
    _seed_external_db(
        corpus,
        [
            ("L-immut1", "consolidated estate knowledge about photon collimation"),
            ("L-immut2", "consolidated estate knowledge about photon scattering"),
        ],
    )

    before_rows = _snapshot_memories_rows(corpus)
    before_hash = _content_hash(before_rows)
    assert len(before_rows) == 2  # guard: snapshot actually captured the seeded rows

    monkeypatch.setenv("TRW_EXTRA_READ_STORES", f'["{corpus}"]')
    _reset_config()

    # Drive a real federated recall (and a wildcard list-path) that touch the corpus.
    surfaced = _ids(memory_adapter.recall_learnings(repo, "photon collimation knowledge", max_results=10))
    assert any(i.startswith("L-immut") for i in surfaced), surfaced
    memory_adapter.recall_learnings(repo, "*", max_results=50)

    after_rows = _snapshot_memories_rows(corpus)
    after_hash = _content_hash(after_rows)
    assert after_rows == before_rows, "external corpus content rows must be unchanged after recall"
    assert after_hash == before_hash


def test_external_corpus_content_immutable_under_write_guard_attempt(tmp_path: Path) -> None:
    """NFR02: even an explicit write attempt routed at the external backend is rejected.

    The write-target guard raises BEFORE any mutation, so the corpus content rows stay
    byte-identical. Proves the app-level read-only enforcement (OQ-04 defense-in-depth)
    actually protects content, not just that it raises.
    """
    from trw_mcp.state import _external_store

    corpus = tmp_path / "estate" / "memory.db"
    _seed_external_db(corpus, [("L-guard", "consolidated estate record about ion drives")])
    before = _snapshot_memories_rows(corpus)
    before_hash = _content_hash(before)

    cfg = TRWConfig(extra_read_stores=[str(corpus)])
    ext = _external_store.get_external_backends(cfg)[0]
    with pytest.raises(PermissionError):
        _external_store.assert_writable_backend(ext)

    after = _snapshot_memories_rows(corpus)
    assert after == before
    assert _content_hash(after) == before_hash


def test_external_corpus_content_immutable_under_repeated_federation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR02: repeated federation cycles do not accumulate any content mutation.

    Guards against a drift where each recall increments an access/recall counter on the
    external rows. After several federation cycles the content hash is unchanged.
    """
    repo = _trw_dir(tmp_path, "proj")
    memory_adapter.store_learning(repo, "L-proj1", "unrelated local note", "x")
    corpus = tmp_path / "estate" / "default" / "memory.db"
    _seed_external_db(corpus, [("L-rep1", "consolidated estate record about graviton lensing")])

    before_hash = _content_hash(_snapshot_memories_rows(corpus))
    monkeypatch.setenv("TRW_EXTRA_READ_STORES", f'["{corpus}"]')
    _reset_config()

    for _ in range(3):
        memory_adapter.recall_learnings(repo, "graviton lensing record", max_results=10)

    assert _content_hash(_snapshot_memories_rows(corpus)) == before_hash


# --------------------------------------------------------------------------- NFR04


def test_external_store_attached_event_emitted(tmp_path: Path) -> None:
    from trw_mcp.state import _external_store

    db = tmp_path / "c" / "memory.db"
    _seed_external_db(db, [("L-1", "one"), ("L-2", "two")])
    cfg = TRWConfig(extra_read_stores=[str(db)])
    with capture_logs() as logs:
        _external_store.get_external_backends(cfg)
    attached = [e for e in logs if e["event"] == "external_store_attached"]
    assert attached and Path(str(attached[0]["path"])) == db.resolve()


def test_logger_module_name() -> None:
    from trw_mcp.state import _external_store

    assert isinstance(_external_store.logger, structlog.stdlib.BoundLogger) or hasattr(_external_store.logger, "info")
