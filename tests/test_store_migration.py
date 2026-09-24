"""PRD-CORE-280 FR03 -- ``trw-mcp memory migrate --to user`` and its rollback, against a real daemon.

Each acceptance criterion is one test: the preview writes nothing, --apply
refuses without an attached daemon and when another process holds the project
store, a completed apply moves rows, vectors and edges through the daemon (never
opening the user store) and pins last, a rerun after a kill before the pin
completes without duplicates, and a rollback restores the store with the rows
written after cutover.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant
from trw_memory.daemon.client import DaemonClient
from trw_memory.models.memory import MemoryEntry
from trw_memory.namespaces.identity import resolve_project_identity
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._memory_fixtures import MemoryDaemon
from trw_mcp.models.config import reload_config
from trw_mcp.state import _store_migration
from trw_mcp.state._store_migration import (
    MigrationRefusedError,
    apply_migration,
    preview_migration,
    rollback_migration,
)
from trw_mcp.state._tier_routing import USER_NAMESPACE

pytestmark = pytest.mark.integration

_IDS = ["L-a", "L-b", "L-c"]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout whose project store holds three rows, their vectors and one edge, all in ``default``."""
    from trw_memory._graph_primitives import _upsert_edge

    root = tmp_path / "repo"
    (root / ".trw" / "memory").mkdir(parents=True)
    (root / ".trw" / "config.yaml").write_text("# operator comment\ntask_root: docs\n", encoding="utf-8")
    store = SQLiteBackend(root / ".trw" / "memory" / "memory.db")
    try:
        for index, entry_id in enumerate(_IDS):
            store.store(MemoryEntry(id=entry_id, content=f"learning {entry_id}", namespace="default", sync_seq=index))
            vector = [0.0] * store._dim
            vector[index] = 1.0
            store.upsert_vector(entry_id, vector, namespace="default")
        with store._lock:
            _upsert_edge(store._conn, "L-a", "L-b", "related_to", 0.5, "2026-09-23T00:00:00+00:00", namespace="default")
            store._conn.commit()
    finally:
        store.close()
    return root


@pytest.fixture
def daemon(memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryDaemon]:
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))

    def _no_autostart(_paths: DaemonPaths) -> None:
        raise AssertionError("a test tried to start a second memory daemon")

    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    reload_config()
    yield memory_daemon
    reload_config()


def _namespace(root: Path) -> str:
    return resolve_project_identity(root).namespace


def _client(root: Path) -> DaemonClient:
    from trw_memory.daemon import read_checkout_grant

    return DaemonClient(read_checkout_grant(root))


def _served(checkout: Path, namespace: str) -> tuple[int, int, int]:
    """``(rows, vectors, edges)`` of *namespace*, as the daemon serving it reports them.

    Asked of the daemon, never read from its file: a second process writing the
    daemon's live WAL store hits SQLite's WAL-reset bug below 3.51.3 (the Linux CI
    image), which the daemon's open then quarantines as corruption.
    """
    health = asyncio.run(_client(checkout).status(namespace))["health"]
    return int(health["entries"]), int(health["embedded"]), int(health["edges"])


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under .trw, byte for byte -- except SQLite's WAL sidecars, which a reader creates."""
    files = sorted((root / ".trw").rglob("*"))
    return {p.name: p.read_bytes() for p in files if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def test_the_preview_writes_nothing_and_lists_counts_and_collisions(checkout: Path, daemon: MemoryDaemon) -> None:
    namespace = _namespace(checkout)
    token = mint_grant(daemon.paths, [namespace, USER_NAMESPACE], root=checkout)
    write_checkout_grant(checkout / ".trw", token)
    asyncio.run(DaemonClient(token).store("already here", namespace, entry_id="L-b"))
    before = _snapshot(checkout)

    preview = preview_migration(checkout / ".trw")

    assert preview == {"namespace": namespace, "rows": {"default": 3}, "vectors": 3, "edges": 1, "collisions": ["L-b"]}
    assert _snapshot(checkout) == before


def test_apply_without_an_attached_daemon_names_doctor_and_changes_nothing(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "no-daemon"))
    before = _snapshot(checkout)

    with pytest.raises(MigrationRefusedError, match="trw-mcp doctor"):
        apply_migration(checkout / ".trw")

    assert _snapshot(checkout) == before


def test_apply_refuses_a_project_store_another_process_holds(checkout: Path, daemon: MemoryDaemon) -> None:
    db = checkout / ".trw" / "memory" / "memory.db"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import sqlite3,time; c=sqlite3.connect({str(db)!r}); c.execute('select 1 from memories').fetchall(); print('ready', flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        before = _snapshot(checkout)
        with pytest.raises(MigrationRefusedError, match="open in another process"):
            apply_migration(checkout / ".trw")
        assert _snapshot(checkout) == before
    finally:
        holder.kill()
        holder.wait()


def test_apply_moves_everything_through_the_daemon_and_pins_last(
    checkout: Path, daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    real_connect = sqlite3.connect

    def _recording(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sqlite3, "connect", _recording)
    namespace = _namespace(checkout)

    manifest_path = apply_migration(checkout / ".trw")
    by_migration = list(opened)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = Path(manifest["backup"])
    assert backup.is_file()
    assert manifest["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    moved = sorted((row["id"], row["from"], row["to"]) for row in manifest["rows"])
    assert moved == [(entry_id, "default", namespace) for entry_id in _IDS]
    assert _served(checkout, namespace) == (3, 3, 1)
    config = (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert f"project_namespace: {namespace}" in config and "# operator comment" in config
    assert not any(Path(path).resolve() == daemon.paths.store.resolve() for path in by_migration if "://" not in path)
    assert not list((checkout / ".trw" / "memory").glob("migration-work-*")), "the working copy is removed"


def test_the_stores_canary_decoys_stay_behind(checkout: Path, daemon: MemoryDaemon) -> None:
    """The decoys guard the project store; the daemon's store seeds its own, so they are not learnings to move."""
    store = SQLiteBackend(checkout / ".trw" / "memory" / "memory.db")
    try:
        store.store(MemoryEntry(id="C-1", content="decoy", namespace="default", metadata={"system_canary": "true"}))
    finally:
        store.close()
    namespace = _namespace(checkout)

    assert preview_migration(checkout / ".trw")["rows"] == {"default": 3}
    manifest = json.loads(apply_migration(checkout / ".trw").read_text(encoding="utf-8"))

    assert sorted(row["id"] for row in manifest["rows"]) == _IDS
    assert _served(checkout, namespace) == (3, 3, 1)


def test_an_id_the_namespace_holds_with_other_content_refuses_the_cutover(checkout: Path, daemon: MemoryDaemon) -> None:
    """The destination wins a collision, so a differing source row would survive only in the backup."""
    namespace = _namespace(checkout)
    token = mint_grant(daemon.paths, [namespace, USER_NAMESPACE], root=checkout)
    write_checkout_grant(checkout / ".trw", token)
    asyncio.run(DaemonClient(token).store("a different learning", namespace, entry_id="L-b"))

    with pytest.raises(MigrationRefusedError, match="L-b"):
        apply_migration(checkout / ".trw")

    assert "project_namespace" not in (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    store = SQLiteBackend(checkout / ".trw" / "memory" / "memory.db")
    try:
        assert store.count(namespace="default") == 3, "the project store is not emptied"
    finally:
        store.close()


def test_an_apply_killed_before_the_pin_completes_on_rerun_without_duplicates(
    checkout: Path, daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = _namespace(checkout)
    real_set_pin = _store_migration._set_pin

    def _killed(_trw_dir: Path, _namespace: str | None) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_store_migration, "_set_pin", _killed)
    with pytest.raises(KeyboardInterrupt):
        apply_migration(checkout / ".trw")
    assert "project_namespace" not in (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    (stranded,) = (checkout / ".trw" / "memory").glob("migration-*.json")
    assert "cutover_at" in json.loads(stranded.read_text(encoding="utf-8")), "the pin is the last write"
    with pytest.raises(MigrationRefusedError, match="not pinned"):
        rollback_migration(checkout / ".trw", stranded)  # nothing was cut over; --apply resumes instead

    monkeypatch.setattr(_store_migration, "_set_pin", real_set_pin)
    apply_migration(checkout / ".trw")

    assert _served(checkout, namespace) == (3, 3, 1)
    page = asyncio.run(_client(checkout).list_page(namespace, 100, None))
    assert sorted(row["id"] for row in page["entries"]) == _IDS, "no duplicate rows"


@pytest.fixture
def migrated(checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path, str]]:
    """``(manifest, user store, namespace)`` after --apply against a daemon of this test's own, now stopped."""
    from tests._memory_daemon import running_daemon

    user_dir = tmp_path / "userhome"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", lambda _paths: None)
    reload_config()
    with running_daemon(user_dir) as paths:
        manifest = apply_migration(checkout / ".trw")
    yield manifest, paths.store, _namespace(checkout)
    reload_config()


def _edit_served(store_path: Path, namespace: str) -> None:
    """After cutover: a new row with a vector, a re-embedded row, an edge added and the migrated edge deleted."""
    from trw_memory._graph_primitives import _upsert_edge
    from trw_memory.integrations._backend import create_backend_from_config
    from trw_memory.models.config import MemoryConfig

    served = create_backend_from_config(MemoryConfig(), namespace, db_path_override=store_path)
    try:
        served.store(MemoryEntry(id="L-late", content="written after cutover", namespace=namespace))
        late, fresh = [0.0] * served._dim, [0.0] * served._dim
        late[5], fresh[7] = 1.0, 1.0
        served.upsert_vector("L-late", late, namespace=namespace)
        served.upsert_vector("L-c", fresh, namespace=namespace)  # a vector-only change
        with served._lock:
            _upsert_edge(
                served._conn, "L-c", "L-a", "related_to", 0.4, "2026-09-23T01:00:00+00:00", namespace=namespace
            )
            served._conn.execute(
                "DELETE FROM memory_graph_edges WHERE namespace = ? AND source_id = 'L-a'", (namespace,)
            )
            served._conn.commit()
    finally:
        served.close()


def _project(checkout: Path) -> tuple[list[str], dict[str, list[float]], set[tuple[str, str]]]:
    store = SQLiteBackend(checkout / ".trw" / "memory" / "memory.db")
    try:
        ids = sorted(e.id for e in store.list_entries(namespace="default", limit=100))
        vectors = store.get_stored_embeddings(ids, namespace="default")
        return ids, vectors, {(e.source_id, e.target_id) for e in store.graph_edges("default")}
    finally:
        store.close()


def test_a_rollback_is_the_exact_inverse_of_the_namespace_now(checkout: Path, migrated: tuple[Path, Path, str]) -> None:
    manifest_path, store_path, namespace = migrated
    _edit_served(store_path, namespace)

    restored = rollback_migration(checkout / ".trw", manifest_path)

    ids, vectors, edges = _project(checkout)
    assert (restored, ids) == (4, [*_IDS, "L-late"])
    assert (vectors["L-late"][5], vectors["L-c"][7], vectors["L-c"][2]) == (1.0, 1.0, 0.0), "no stale vector"
    assert edges == {("L-c", "L-a")}, "the added edge is back and the deleted one stays deleted"
    assert "project_namespace" not in (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert Path(json.loads(manifest_path.read_text(encoding="utf-8"))["backup"]).is_file()


def test_a_stop_mid_swap_is_resumed_by_rerunning_the_rollback(
    checkout: Path, migrated: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, store_path, namespace = migrated
    _edit_served(store_path, namespace)
    real_swap = _store_migration._swap

    def _stopped(_trw_dir: Path, _restored: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_store_migration, "_swap", _stopped)
    with pytest.raises(KeyboardInterrupt):
        rollback_migration(checkout / ".trw", manifest_path)
    assert "project_namespace" in (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(_store_migration, "_swap", real_swap)

    assert rollback_migration(checkout / ".trw", manifest_path) == 4
    assert _project(checkout)[0] == [*_IDS, "L-late"]
    assert not list((checkout / ".trw" / "memory").glob("migration-rollback-*")), "no staging left behind"


_CLAIM = (
    "from trw_memory.daemon import DaemonPaths\n"
    "from trw_memory.daemon._instance import claim_single_instance\n"
    "print('ready', flush=True)\n"
    "claim_single_instance(DaemonPaths.resolve(), port=0, version='test')\n"
)


def test_no_daemon_can_start_until_the_rollback_has_removed_the_pin(
    checkout: Path, migrated: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = migrated[0]
    real_set_pin = _store_migration._set_pin
    starting: list[bool] = []

    def _restart_at_the_boundary(trw_dir: Path, namespace: str | None) -> None:
        restart = subprocess.Popen([sys.executable, "-c", _CLAIM], stdout=subprocess.PIPE, text=True)
        try:
            assert restart.stdout is not None and restart.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                restart.wait(timeout=1.5)
            starting.append(True)
        finally:
            restart.kill()
            restart.wait()
        real_set_pin(trw_dir, namespace)

    monkeypatch.setattr(_store_migration, "_set_pin", _restart_at_the_boundary)

    assert rollback_migration(checkout / ".trw", manifest_path) == 3
    assert starting == [True], "a daemon starting while the pin is still set waits for the rollback's lock"


def test_a_rollback_refuses_a_user_store_other_than_the_one_migrated_into(
    checkout: Path, migrated: tuple[Path, Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = migrated[0]
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "another-home"))
    before = _snapshot(checkout)

    with pytest.raises(MigrationRefusedError, match="TRW_USER_DIR"):
        rollback_migration(checkout / ".trw", manifest_path)

    assert _snapshot(checkout) == before
    assert not DaemonPaths.resolve().store.exists(), "the rollback never creates a store"


def test_a_rollback_refuses_a_missing_user_store_and_creates_none(
    checkout: Path, migrated: tuple[Path, Path, str]
) -> None:
    manifest_path, store_path, _namespace = migrated
    for sidecar in (store_path, *store_path.parent.glob(f"{store_path.name}-*")):
        sidecar.unlink()
    before = _snapshot(checkout)

    with pytest.raises(MigrationRefusedError, match="missing"):
        rollback_migration(checkout / ".trw", manifest_path)

    assert _snapshot(checkout) == before
    assert not store_path.exists(), "the rollback never creates a store"


def test_a_rollback_refuses_while_the_daemon_runs(
    checkout: Path, daemon: MemoryDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    manifest_path = apply_migration(checkout / ".trw")
    before = _snapshot(checkout)
    args = _build_arg_parser().parse_args(
        ["memory", "migrate", "--to", "user", "--target-dir", str(checkout), "--rollback", str(manifest_path)]
    )

    with pytest.raises(SystemExit) as exited:
        SUBCOMMAND_HANDLERS[args.command](args)

    assert exited.value.code == 2
    assert "stop the memory daemon first" in capsys.readouterr().err
    assert _snapshot(checkout) == before


def test_the_verb_previews_by_default_and_exits_naming_doctor_when_apply_is_refused(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "no-daemon"))

    def run(*argv: str) -> None:
        args = _build_arg_parser().parse_args(
            ["memory", "migrate", "--to", "user", "--target-dir", str(checkout), *argv]
        )
        SUBCOMMAND_HANDLERS[args.command](args)

    run()
    assert json.loads(capsys.readouterr().out)["rows"] == {"default": 3}
    with pytest.raises(SystemExit) as exited:
        run("--apply")
    assert exited.value.code == 1
    assert "trw-mcp doctor" in capsys.readouterr().err
