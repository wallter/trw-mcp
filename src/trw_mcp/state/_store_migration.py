"""``trw-mcp memory migrate`` -- move a checkout's project store into the user store and back (PRD-CORE-280 FR03).

The only way a project moves; nothing migrates on open.

* **Preview** (the default) writes nothing: per-namespace row counts of the
  project store, and -- when the checkout already holds a grant -- which of its
  ids the destination namespace already has (the destination wins those).
* **Apply** goes through the daemon, starting one as every memory client does
  when none is running; trw-mcp never opens the user store. Preflight
  refuses a pinned checkout whose project store holds no rows, a sync replay in
  progress or an unreadable sync-state, and a project store another process
  holds. Holding the store exclusively, it takes a sha256-recorded online
  backup, writes a manifest, hands the daemon a working copy inside the checkout
  (``memory_import_checkout`` merges it; the destination wins and a rerun moves
  nothing twice), verifies the migrated ids' row, vector and edge counts over
  the daemon, and only then pins ``project_namespace``. Then it empties the
  project store: its rows now live in the daemon, and the backup keeps them.
* A **pinned** checkout whose project store still holds rows (a split store,
  e.g. rows an older stdio server wrote after the cutover) takes the same
  apply: its strays merge into the pinned namespace through the checkout's
  existing grant, which must cover that namespace, and the pin is left as it is
  (PRD-CORE-280 FR06).
* A refusal the user clears by waiting or stopping something -- another process
  holds the project store, the daemon answered ``busy`` or ``uncertain``, or
  the verified counts fell short -- is a :class:`MigrationRetryError`, which the
  verb exits 2 on; any other refusal exits 1.
* **Rollback** is the exact inverse, run with the daemon stopped: the
  namespace's every row, vector and edge is copied back under ``default`` into
  a fresh store that replaces the project store, then the pin is removed. It
  reads only the user store file --apply recorded (path and inode) and never
  creates one. The pre-migration backup is kept as the fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trw_mcp.state._tier_routing import USER_NAMESPACE

__all__ = [
    "DaemonRunningError",
    "MigrationRefusedError",
    "MigrationRetryError",
    "apply_migration",
    "preview_migration",
    "rollback_migration",
]

_SOURCE = "default"
_PIN = "project_namespace"
_DOCTOR = "run `trw-mcp doctor`"


class MigrationRefusedError(RuntimeError):
    """A preflight or verification refusal; nothing was cut over."""


class MigrationRetryError(MigrationRefusedError):
    """A refusal that a rerun clears once the store is free: nothing was cut over."""


def _store(trw_dir: Path) -> Path:
    return trw_dir / "memory" / "memory.db"


def _pin(trw_dir: Path) -> str | None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import resolve_config_overrides

    return TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml")).project_namespace  # type: ignore[arg-type]


def _set_pin(trw_dir: Path, namespace: str | None) -> None:
    """Write (or, with ``None``, remove) the pin, keeping the rest of config.yaml as it is."""
    from trw_mcp.state._persistence_helpers import _roundtrip_yaml, lock_for_rmw
    from trw_mcp.state.persistence import FileStateWriter

    path = trw_dir / "config.yaml"
    with lock_for_rmw(path):
        yaml = _roundtrip_yaml()
        config = (yaml.load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
        if namespace is None:
            config.pop(_PIN, None)
        else:
            config[_PIN] = namespace
        with io.StringIO() as buffer:
            yaml.dump(config, buffer)
            FileStateWriter().write_text(path, buffer.getvalue())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _namespace(trw_dir: Path) -> str:
    from trw_memory.namespaces.identity import resolve_project_identity

    return resolve_project_identity(trw_dir.parent).namespace


def _daemon_paths(*, start: bool = False) -> Any:
    """The daemon's paths when one runs, or -- with *start* -- when none does and the first call may start one.

    An untrusted record is never started over: it may name a daemon still serving the store.
    """
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._discovery import DaemonInfo, DiscoveryAbsent, read_live_discovery

    paths = DaemonPaths.resolve()
    found = read_live_discovery(paths)
    if isinstance(found, DaemonInfo) or (start and isinstance(found, DiscoveryAbsent)):
        return paths
    raise MigrationRefusedError(f"the memory daemon is not attached; {_DOCTOR}, then retry")


def holds_rows(db: Path) -> bool:
    """Whether the project store *db* holds a learning row (a canary decoy is not one), read-only.

    A file that is absent, 0 bytes, or has no tables holds none. One that cannot
    be read, or holds tables but no ``memories``, is not provably empty, so it
    counts as holding rows. ``immutable=1`` at rest: ``mode=ro`` alone still
    creates ``-wal``/``-shm`` beside a WAL database.
    """
    if not db.is_file() or not db.stat().st_size:
        return False
    at_rest = not db.with_name(db.name + "-wal").exists()
    try:
        with contextlib.closing(
            sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro{'&immutable=1' if at_rest else ''}", uri=True)
        ) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "memories" not in tables:
                return bool(tables)
            return bool(conn.execute(_REAL_ROWS_SQL).fetchone()[0])
    except sqlite3.DatabaseError:  # trw-fail-silent-allow: an unreadable store is not provably empty
        return True


_REAL_ROWS_SQL = (
    "SELECT COUNT(*) FROM memories WHERE "
    "NOT (json_valid(metadata) AND COALESCE(json_extract(metadata, '$.system_canary'), '') = 'true')"
)


def _preflight(trw_dir: Path) -> str | None:
    """Refuse what --apply cannot move; returns the checkout's pin (``None`` when unpinned)."""
    if (pinned := _pin(trw_dir)) and not holds_rows(_store(trw_dir)):
        raise MigrationRefusedError(f"{trw_dir.parent} is already migrated to {pinned}; nothing to do")
    state = trw_dir / "sync-state.json"
    try:
        sync = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    except (OSError, ValueError) as exc:
        raise MigrationRefusedError(f"{state} cannot be read ({exc}); fix or remove it, then retry") from exc
    if isinstance(sync, dict) and sync.get("replay"):
        raise MigrationRefusedError("a sync replay is in progress; finish it with `trw-mcp sync pull --full --resume`")
    if not _store(trw_dir).is_file():
        raise MigrationRefusedError(f"{trw_dir.parent} has no project store at {_store(trw_dir)}")
    return pinned


@contextlib.contextmanager
def _exclusive(db: Path) -> Iterator[sqlite3.Connection]:
    """Hold *db* exclusively until the block ends, or refuse when another process has it open."""
    conn = sqlite3.connect(db, timeout=0, isolation_level=None)
    try:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("COMMIT")  # the exclusive locking mode keeps the lock until close
    except sqlite3.OperationalError as exc:
        conn.close()
        raise MigrationRetryError(
            f"{db} is open in another process ({exc}); stop this checkout's other trw-mcp sessions, then retry"
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def _snapshot(conn: sqlite3.Connection, target: Path) -> None:
    with contextlib.closing(sqlite3.connect(target)) as copy:
        conn.backup(copy)


def _source_rows(db: Path) -> tuple[dict[str, int], list[Any], int, int]:
    """``(rows per namespace, the default rows, their vectors, their edges)`` of a store copy.

    The copy's canary decoys are dropped first: they guard this store, not the
    user store (which seeds its own), and they are not learnings.
    """
    from trw_memory.storage.interface import EntryCursor
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    store = SQLiteBackend(db)
    try:
        decoys = store.list_entries(namespace=_SOURCE, entry_filter=lambda e: e.metadata.get("system_canary") == "true")
        for decoy in decoys:
            store.delete(decoy.id, namespace=_SOURCE)
        counts = {ns: store.count(namespace=ns) for ns in store.list_namespaces()}
        rows: list[Any] = []
        cursor: EntryCursor | None = None
        while page := store.list_entries(namespace=_SOURCE, limit=1000, after=cursor):
            rows.extend(page)
            cursor = EntryCursor.from_entry(page[-1])
        vectors = len(store.existing_vector_ids(namespace=_SOURCE)) if store.supports_vectors() else 0
        return counts, rows, vectors, len(store.graph_edges(_SOURCE))
    finally:
        store.close()


def _client(trw_dir: Path, namespace: str, paths: Any, *, mint: bool) -> Any:
    from trw_memory.daemon import mint_grant, read_checkout_grant, write_checkout_grant
    from trw_memory.daemon._grants import granted_namespaces
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.exceptions import DaemonAuthError

    if mint:
        token = mint_grant(paths, [namespace, USER_NAMESPACE], root=trw_dir.parent)
        write_checkout_grant(trw_dir, token)
        return DaemonClient(token, paths=paths)
    try:
        token = read_checkout_grant(trw_dir.parent)
    except DaemonAuthError as exc:
        raise MigrationRefusedError(str(exc)) from exc
    if namespace not in (granted_namespaces(paths, token) or ()):
        raise MigrationRefusedError(f"this checkout's grant does not cover {namespace}; run `trw-mcp memory token`")
    return DaemonClient(token, paths=paths)


def _call(coroutine: Any) -> dict[str, Any]:
    answer: dict[str, Any] = asyncio.run(coroutine)
    status = answer.get("status")
    if status != "ok":
        refusal = MigrationRetryError if status in {"busy", "uncertain"} else MigrationRefusedError
        raise refusal(f"the daemon refused ({status}): {answer.get('error')}; {_DOCTOR}")
    return answer


def preview_migration(trw_dir: Path) -> dict[str, object]:
    """What ``--apply`` would move, writing nothing."""
    namespace = _preflight(trw_dir) or _namespace(trw_dir)
    with contextlib.ExitStack() as stack:
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "memory.db"
        with contextlib.closing(sqlite3.connect(f"file:{_store(trw_dir)}?mode=ro", uri=True)) as live:
            _snapshot(live, scratch)
        counts, rows, vectors, edges = _source_rows(scratch)
    collisions: list[str] | None = None
    with contextlib.suppress(Exception):  # trw-fail-silent-allow: a preview without a grant or daemon says so (None)
        held = {e.id for e in _daemon_rows(_client(trw_dir, namespace, _daemon_paths(), mint=False), namespace)}
        collisions = sorted(held & {r.id for r in rows})
    return {"namespace": namespace, "rows": counts, "vectors": vectors, "edges": edges, "collisions": collisions}


def apply_migration(trw_dir: Path) -> Path:
    """Move the project store through the daemon and pin the checkout; returns the manifest path."""
    pinned = _preflight(trw_dir)
    paths = _daemon_paths(start=True)
    # Microseconds: a strays pass can follow its migration within the second, and must not overwrite its backup.
    namespace, stamp = pinned or _namespace(trw_dir), _now().strftime("%Y%m%dT%H%M%S%fZ")
    memory_dir = trw_dir / "memory"
    backup, work = memory_dir / f"memory.db.pre-user-{stamp}", memory_dir / f"migration-work-{stamp}" / "memory.db"
    manifest_path = memory_dir / f"migration-{stamp}.json"
    with _exclusive(_store(trw_dir)) as conn:
        _snapshot(conn, backup)
        work.parent.mkdir()
        shutil.copyfile(backup, work)
        try:
            counts, rows, vectors, edges = _source_rows(work)
            if strays := sorted(set(counts) - {_SOURCE}):
                raise MigrationRefusedError(f"the project store holds rows outside {_SOURCE!r}: {strays}")
            manifest: dict[str, object] = {
                "namespace": namespace,
                "backup": str(backup),
                "backup_sha256": _sha256(backup),
                "rows": [_manifest_row(entry, namespace) for entry in rows],
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            client = _client(trw_dir, namespace, paths, mint=not pinned)
            found = _call(client.import_checkout(namespace, str(work), [entry.id for entry in rows]))["held"]
            expected = {"rows": len(rows), "vectors": vectors, "edges": edges}
            # Every id must land. Vectors and edges may exceed the copy's: an id the
            # destination already held keeps its own (the destination wins).
            if found["rows"] != len(rows) or found["vectors"] < vectors or found["edges"] < edges:
                raise MigrationRetryError(
                    f"the daemon holds {found} of the migrated ids, not {expected}; config.yaml and the project "
                    f"store are untouched -- rerun --apply, or {_DOCTOR}"
                )
            manifest["user_store"] = {"path": str(paths.store.resolve()), "identity": _identity(paths.store)}
            # The cutover is recorded before the pin, its last write: a stop in between leaves an unpinned
            # checkout that --apply resumes, never a pin whose manifest cannot roll back.
            manifest["cutover_at"] = _now().isoformat()
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if not pinned:
                _set_pin(trw_dir, namespace)
            with contextlib.closing(sqlite3.connect(":memory:")) as empty:
                empty.backup(conn)  # the project store now holds nothing; the backup keeps what it held
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)
    return manifest_path


def _manifest_row(entry: Any, namespace: str) -> dict[str, object]:
    return {
        "id": entry.id,
        "from": _SOURCE,
        "to": namespace,
        "remote_id": entry.remote_id,
        "sync_seq": entry.sync_seq,
        "sync_hash": entry.sync_hash,
        "vector_clock": entry.vector_clock,
    }


def rollback_migration(trw_dir: Path, manifest_path: Path) -> int:
    """Put the namespace back into the project store, exactly; returns the rows restored.

    The inverse of ``--apply``, run with the daemon stopped: holding the daemon's own
    lock (so none can start), it copies the namespace's every row, vector and edge
    under ``default`` into a fresh store, checks the counts, swaps it in while the
    project store is held exclusively, and only then removes the pin. The
    pre-migration backup stays as the fallback. A stop anywhere before the pin is
    removed is resumed by rerunning the rollback.
    """
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._discovery import DaemonInfo, DiscoveryInvalid, read_discovery_result
    from trw_memory.storage.persistence import lock_for_rmw

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    namespace, backup = str(manifest["namespace"]), Path(manifest["backup"])
    if _pin(trw_dir) != namespace:
        raise MigrationRefusedError(f"{trw_dir.parent} is not pinned to {namespace}; nothing to roll back")
    if "cutover_at" not in manifest or _sha256(backup) != manifest["backup_sha256"]:
        raise MigrationRefusedError(f"{manifest_path} has no completed cutover, or its backup {backup} changed")
    paths = DaemonPaths.resolve()
    with lock_for_rmw(paths.lock_anchor):  # held until the pin is gone, so no daemon starts in between
        found = read_discovery_result(paths)
        if isinstance(found, DiscoveryInvalid) or (isinstance(found, DaemonInfo) and found.is_live(paths.lock)):
            raise DaemonRunningError("stop the memory daemon first; a rollback copies its store while nothing writes")
        _check_user_store(paths.store, manifest)
        staging = trw_dir / "memory" / f"migration-rollback-{_now().strftime('%Y%m%dT%H%M%SZ')}"
        staging.mkdir()
        try:
            restored = _copy_namespace(paths.store, namespace, staging / "memory.db")
            _swap(trw_dir, staging / "memory.db")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        _set_pin(trw_dir, None)
    return restored


def _identity(store: Path) -> list[int]:
    stat = store.stat()
    return [stat.st_dev, stat.st_ino]


def _check_user_store(store: Path, manifest: dict[str, Any]) -> None:
    """Refuse unless *store* is the very file --apply moved the namespace into; nothing here creates one."""
    recorded = manifest.get("user_store") or {}
    if str(store.resolve()) != recorded.get("path"):
        raise MigrationRefusedError(
            f"this environment's user store is {store}, but the migration went into {recorded.get('path')}; "
            "set TRW_USER_DIR back to the one --apply used, then retry"
        )
    if not store.is_file() or _identity(store) != recorded.get("identity"):
        raise MigrationRefusedError(f"{store} is missing or is not the file --apply migrated into; {_DOCTOR}")


class DaemonRunningError(MigrationRetryError):
    """A rollback was asked for while a memory daemon may still write the user store."""


def _census(store: Any, namespace: str) -> tuple[int, int, int]:
    return (
        store.count(namespace=namespace),
        len(store.existing_vector_ids(namespace)),
        len(store.graph_edges(namespace)),
    )


def _copy_namespace(user_store: Path, namespace: str, target: Path) -> int:
    """Copy every row, vector and edge of *namespace* under ``default`` into a fresh store at *target*."""
    from trw_memory.integrations._backend import create_backend_from_config
    from trw_memory.models.config import MemoryConfig
    from trw_memory.storage.interface import EntryCursor
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    source = create_backend_from_config(MemoryConfig(), namespace, db_path_override=user_store)
    copy = SQLiteBackend(target, dim=getattr(source, "_dim", 384))
    try:
        ids: list[str] = []
        cursor: EntryCursor | None = None
        with copy.transaction():
            while page := source.list_entries(namespace=namespace, limit=1000, after=cursor):
                for entry in page:
                    copy.store(entry.model_copy(update={"namespace": _SOURCE}))
                ids.extend(entry.id for entry in page)
                cursor = EntryCursor.from_entry(page[-1])
            records = source.get_vector_records(ids, namespace=namespace)
            for entry_id, embedding in source.get_stored_embeddings(ids, namespace=namespace).items():
                record = records.get(entry_id)
                proof = {"provenance": record.provenance} if record is not None and record.provenance else {}
                copy.upsert_vector(entry_id, embedding, namespace=_SOURCE, **proof)
            copy.add_graph_edges(_SOURCE, source.graph_edges(namespace))
        if (held := _census(copy, _SOURCE)) != (want := _census(source, namespace)):
            raise MigrationRefusedError(f"the copy holds {held} (rows, vectors, edges), not {want}; nothing changed")
        return held[0]
    finally:
        copy.close()
        source.close()


def _swap(trw_dir: Path, restored: Path) -> None:
    """Replace the project store's content with *restored* in one backup step, holding the store exclusively."""
    with _exclusive(_store(trw_dir)) as conn, contextlib.closing(sqlite3.connect(restored)) as source:
        source.backup(conn)


def _daemon_rows(client: Any, namespace: str) -> list[Any]:
    from trw_mcp.state._daemon_store import _entry

    rows: list[Any] = []
    after: dict[str, str] | None = None
    while True:
        page = _call(client.list_page(namespace, 500, after))
        rows.extend(_entry(row) for row in page["entries"])
        if not (after := page.get("next")):
            return rows
