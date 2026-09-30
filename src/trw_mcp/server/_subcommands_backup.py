"""``trw-mcp backup create`` / ``trw-mcp backup restore`` (PRD-CORE-311 FR05/FR06).

Belongs to the ``_subcommands.py`` facade (dispatched lazily via
``SUBCOMMAND_HANDLERS["backup"]``). Follows the ``_doctor_*``/``_subcommands_doctor.py``
split pattern named in the PRD: a thin CLI dispatcher over trw-memory's own
local-archive/restore primitives plus :mod:`trw_mcp.sync.backup`'s
presigned-transport client, no new detection or transport logic here.

``backup create`` chains FR01 (local gzip archive, trw-memory) and FR03 (the
presigned-PUT upload, gated on ``backup_remote_enabled``) in one invocation:
the local archive is created unconditionally and its path is always printed;
the remote leg only runs, and its object key is only printed, when consented.
A failed remote leg never deletes or invalidates the local archive.

``backup restore --from latest`` is the PRD's own Definition of Done: it lists
remote backups (newest first), downloads the newest via a presigned GET, and
delegates to trw-memory's ``restore_from_archive`` (gunzip + sha256 verify +
the existing ``restore_from_snapshot`` atomic replace). ``--from <path>`` is
the offline drill (NFR01): a local archive path is restored with zero network
calls — the same trw-memory helper, given a path already on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._platform_trust import payload_trw_dir, send_policy_all
from trw_mcp.sync.backup import BackupUploader

__all__ = ["run_backup"]


class _RestoreCliError(Exception):
    """A restore-drill failure the CLI reports and exits non-zero on. Never a traceback."""


def _load_config() -> TRWConfig:
    """Resolve the effective config. A patchable seam — tests monkeypatch this name directly."""
    return TRWConfig()


def _resolve_base_and_db(args: argparse.Namespace) -> tuple[Path, Path]:
    """``--db``, else the store the daemon serves, through the daemon's own resolver.

    E2E-BACKUP-DAEMON-STORE-ONE-RESOLVER: ``served_store_path`` is the one rule both use, so a backup
    always archives the file the daemon serves. An explicit ``MEMORY_STORAGE_PATH`` that names another
    store is not honoured by the daemon, so it is named in a warning rather than silently ignored.
    """
    if getattr(args, "db", None):
        db_path = Path(args.db).resolve()
        return db_path.parent, db_path
    from trw_memory.daemon import served_store_path
    from trw_memory.integrations._backend import resolve_backend_db_path
    from trw_memory.models.config import MemoryConfig

    db_path = served_store_path().resolve()
    config = MemoryConfig()
    if "storage_path" in config.model_fields_set and not config.memory_single_store_path:
        named = resolve_backend_db_path(config, "default").resolve()
        if named != db_path:
            print(
                f"warning: MEMORY_STORAGE_PATH names {named}, but the daemon serves {db_path}; "
                "backing up the served store (pass --db to archive another file)",
                file=sys.stderr,
            )
    return db_path.parent, db_path


def _confirm_replace(db_path: Path, args: argparse.Namespace) -> None:
    """Refuse to overwrite *db_path* unless ``--yes`` was given or an interactive user says yes (HB-2)."""
    what = f"{db_path} (the whole store: every project's memory in it)"
    if getattr(args, "yes", False):
        return
    if sys.stdin.isatty():
        answer = input(f"backup restore will replace {what}.\nThe current store is archived first. Continue? [y/N] ")
        if answer.strip().lower() in {"y", "yes"}:
            return
    print(f"backup restore: not replacing {what}; re-run with --yes to confirm", file=sys.stderr)
    sys.exit(2)


def _invoking_trw_dir() -> Path | None:
    """The invoking project's ``.trw``: ``MemoryConfig``'s, the nearest at or above the cwd, never env-named."""
    from trw_memory.models.config import MemoryConfig

    return MemoryConfig().source_trw_dir


def _build_uploader(config: TRWConfig, client_id: str | None = None, *, source_trw_dir: Path | None) -> BackupUploader:
    return BackupUploader(
        backend_url=config.resolved_backend_url,
        api_key=config.resolved_backend_api_key,
        client_id=client_id,
        backup_remote_enabled=config.backup_remote_enabled,
        source_trw_dir=source_trw_dir,
    )


def _run_backup_create(args: argparse.Namespace) -> None:
    from trw_memory.cli_client import refused_beside_daemon
    from trw_memory.storage._backup_archive import create_backup_archive

    base_dir, db_path = _resolve_base_and_db(args)
    if refused_beside_daemon("backup create"):
        sys.exit(1)
    if not db_path.exists():
        print(f"Source DB does not exist: {db_path}", file=sys.stderr)
        sys.exit(1)

    archive = create_backup_archive(base_dir, db_path)
    print(f"Archived store: {db_path}")
    print(f"Created backup archive: {archive.path}")

    # The store FILE's own project and the invoking project must BOTH allow it: --db (or a single-store
    # path) can place the store in another project, which may restrict the backup but never authorize it.
    owner = payload_trw_dir(db_path)
    policy = send_policy_all((_invoking_trw_dir(), owner))
    if owner is None or not (policy.contact and policy.backup_remote):
        print("Local-only: the store's project and the invoking project must both allow a remote backup.")
        return
    uploader = _build_uploader(_load_config(), source_trw_dir=owner)
    result = asyncio.run(uploader.upload(archive.path))
    if result.status == "ok":
        print(f"Uploaded to remote backup: {result.key}")
    elif result.status == "disabled":
        print("Local-only: backup_remote_enabled is False, no remote upload attempted.")
    else:
        print(
            f"Remote upload failed ({result.status}); the local archive is retained at {archive.path}.",
            file=sys.stderr,
        )


async def _fetch_latest_remote_archive(uploader: BackupUploader, staging_dir: Path) -> tuple[Path, str]:
    """List remote backups, download the newest, and return (local path, remote key). Raises _RestoreCliError."""
    listing = await uploader.list_remote()
    if listing.status != "ok":
        raise _RestoreCliError(f"could not list remote backups (status={listing.status})")
    if not listing.objects:
        raise _RestoreCliError("no remote backups found")

    newest = listing.objects[0]  # backend contract: newest first
    dest = staging_dir / Path(newest.key).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    download = await uploader.download(newest.key, dest)
    if download.status != "ok" or download.path is None:
        raise _RestoreCliError(f"could not download remote backup {newest.key} (status={download.status})")
    return download.path, newest.key


_RAW_COPY_CHUNK = 1024 * 1024


def _copy_streamed(source: Path, base_dir: Path, rel: Path) -> None:
    """Copy *source* to ``base_dir/rel`` a chunk at a time, refusing a symlinked component (PRD-CORE-337).

    The first ``write_beneath`` replaces the leaf (and makes an empty source an empty file); each later chunk is
    an ``append_beneath``. Peak memory is one chunk, not the store. Not atomic: the caller discards a partial copy.
    """
    from trw_memory.safe_fs import append_beneath, write_beneath

    with source.open("rb") as handle:
        chunk = handle.read(_RAW_COPY_CHUNK)
        write_beneath(base_dir, rel, chunk, mode=0o600)
        # One chunk ahead, so the last append can be fsynced (write_beneath syncs a whole file; appends do not).
        following = handle.read(_RAW_COPY_CHUNK)
        while following:
            chunk, following = following, handle.read(_RAW_COPY_CHUNK)
            append_beneath(base_dir, rel, chunk, mode=0o600, sync=not following)


def _keep_current_store(base_dir: Path, db_path: Path) -> None:
    """Keep what a restore will replace: an archive, else a raw copy; exit 1 when neither can be made.

    A corrupt store is restore's main use, and exactly where ``VACUUM INTO`` fails. The byte copy of the
    store and its ``-wal``/``-shm`` is taken FIRST, before anything opens the file: opening a corrupt store
    for the archive discards its WAL, which can hold committed rows. When the archive then succeeds the
    raw copy is dropped. A store the daemon holds (``StoreBusyError``) is refused outright.
    """
    from trw_memory._tree_removal import remove_tree_beneath
    from trw_memory.exceptions import StoreBusyError, UnsafeWriteError
    from trw_memory.storage._backup_archive import BackupArchiveError, backups_base_dir, create_backup_archive

    raw_dir = backups_base_dir(base_dir) / f"pre-restore-raw-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    copy_error: Exception | None = None
    try:
        for suffix in ("", "-wal", "-shm"):
            source = db_path.with_name(db_path.name + suffix)
            if source.exists():
                # Anchored at the store's own directory and symlink-refusing: with --db or a relocated store
                # that directory can be inside a checkout (PRD-CORE-337 census).
                _copy_streamed(source, base_dir, raw_dir.relative_to(base_dir) / source.name)
    except (OSError, UnsafeWriteError) as exc:
        copy_error = exc
        # A streamed copy is not atomic: drop what was written so a truncated WAL is never offered as a copy.
        remove_tree_beneath(base_dir, raw_dir.relative_to(base_dir), purpose="partial pre-restore raw copy")
    try:
        saved = create_backup_archive(base_dir, db_path)
    except StoreBusyError as exc:
        print(f"Backup restore failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except BackupArchiveError as exc:  # trw-fail-silent-allow: falls back to the raw copy it prints
        if copy_error is not None:
            print(
                f"Backup restore failed: could not archive ({exc}) or copy ({copy_error}) the current store; "
                "nothing was replaced. Re-run with --yes --no-snapshot to replace it without a copy.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Saved a raw copy of the current store to {raw_dir} (the archive step failed: {exc})")
        return
    if raw_dir.exists():
        remove_tree_beneath(
            base_dir, raw_dir.relative_to(base_dir), purpose="pre-restore raw copy superseded by the archive"
        )
    print(f"Saved the current store to {saved.path} (restore it with: trw-mcp backup restore --from <that path>)")


def _run_backup_restore(args: argparse.Namespace) -> None:
    """Restore the store from an archive: verify it, then keep the current store, then swap (INC-127, INC-129).

    The archive is decompressed and checked (gzip, sha256 sidecar, SQLite header and ``integrity_check``, the
    ``memories`` table) BEFORE the live store is opened or archived, so a refused source leaves it byte-identical
    and writes no pre-restore archive. The pre-restore copy is taken right before the swap.
    """
    from trw_memory.exceptions import StoreBusyError
    from trw_memory.storage._backup_archive import BackupArchiveError, verified_archive
    from trw_memory.storage._snapshot import SnapshotError, restore_from_snapshot

    base_dir, db_path = _resolve_base_and_db(args)
    source = str(getattr(args, "restore_from", "")).strip()
    if not source:
        print("backup restore: --from is required ('latest' or a local archive path)", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "no_snapshot", False) and not getattr(args, "yes", False):
        print(
            "backup restore: --no-snapshot needs --yes (it replaces the store without keeping a copy)", file=sys.stderr
        )
        sys.exit(2)

    remote_key: str | None = None
    staged: Path | None = None
    if source == "latest":
        owner = payload_trw_dir(db_path)  # as for create: the store's project AND the invoking one
        if owner is None or not send_policy_all((_invoking_trw_dir(), owner)).contact:
            print(
                "Backup restore failed: the store's project and the invoking project must both allow contact",
                file=sys.stderr,
            )
            sys.exit(1)
        uploader = _build_uploader(_load_config(), source_trw_dir=owner)
        try:
            archive_path, remote_key = asyncio.run(
                _fetch_latest_remote_archive(uploader, base_dir / "memory" / "restore-staging")
            )
        except _RestoreCliError as exc:
            print(f"Backup restore failed: {exc}", file=sys.stderr)
            sys.exit(1)
        staged = archive_path
    else:
        archive_path = Path(os.path.abspath(source))  # NOT resolved: a symlinked archive is refused, not followed

    try:
        with verified_archive(archive_path, db_path) as verified:
            _confirm_replace(db_path, args)
            if getattr(args, "no_snapshot", False):
                print(
                    f"WARNING: --no-snapshot: NO copy of the current store was kept before replacing {db_path}",
                    file=sys.stderr,
                )
            elif db_path.exists():
                _keep_current_store(base_dir, db_path)
            try:
                restore_from_snapshot(db_path.parent, verified, db_path)
            except SnapshotError as exc:
                raise BackupArchiveError(f"backup restore failed: {exc}") from exc
    except (BackupArchiveError, StoreBusyError) as exc:
        print(f"Backup restore failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if staged is not None:
            with contextlib.suppress(OSError):
                staged.unlink(missing_ok=True)

    if remote_key is not None:
        print(f"Restored {db_path} from remote backup {remote_key}")
    else:
        print(f"Restored {db_path} from {archive_path}")
    from trw_mcp.server._backup_derived_tiers import restore_derived_tiers, restore_store_warm_tiers

    keep = bool(getattr(args, "keep_derived", False))
    if (store_notice := restore_store_warm_tiers(db_path.parent, keep=keep)) is not None:
        print(store_notice)
    for trw_dir in dict.fromkeys((_invoking_trw_dir(), payload_trw_dir(db_path))):
        notice = restore_derived_tiers(trw_dir, keep=keep)  # INC-128: recall must agree with the restored store
        if notice is not None:
            print(notice)


def run_backup(args: argparse.Namespace) -> None:
    """Dispatch ``backup <subcommand>``."""
    command = getattr(args, "backup_command", None)
    handlers = {"create": _run_backup_create, "restore": _run_backup_restore}
    if command in handlers:
        handlers[command](args)
        return
    print(
        "Usage: trw-mcp backup create [--db PATH]\n"
        "       trw-mcp backup restore --from latest|PATH [--yes [--no-snapshot]] [--keep-derived] [--db PATH]",
        file=sys.stderr,
    )
    sys.exit(2)
