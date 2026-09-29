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
import sys
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
    from trw_memory.cli_storage import resolve_base_and_db
    from trw_memory.models.config import MemoryConfig

    return resolve_base_and_db(args, config_cls=MemoryConfig)


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
    print(f"Created backup archive: {archive.path}")

    # The store FILE's own project and the invoking project must BOTH allow it: MEMORY_STORAGE_PATH or
    # --db can place the store in another project, which may restrict the backup but never authorize it.
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


def _run_backup_restore(args: argparse.Namespace) -> None:
    from trw_memory.exceptions import StoreBusyError
    from trw_memory.storage._backup_archive import BackupArchiveError, restore_from_archive

    base_dir, db_path = _resolve_base_and_db(args)
    source = str(getattr(args, "restore_from", "")).strip()
    if not source:
        print("backup restore: --from is required ('latest' or a local archive path)", file=sys.stderr)
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
        archive_path = Path(source).resolve()

    try:
        restore_from_archive(archive_path, db_path)
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


def run_backup(args: argparse.Namespace) -> None:
    """Dispatch ``backup <subcommand>``."""
    command = getattr(args, "backup_command", None)
    handlers = {"create": _run_backup_create, "restore": _run_backup_restore}
    if command in handlers:
        handlers[command](args)
        return
    print(
        "Usage: trw-mcp backup create [--namespace NAMESPACE] [--db PATH]\n"
        "       trw-mcp backup restore --from latest|PATH [--namespace NAMESPACE] [--db PATH]",
        file=sys.stderr,
    )
    sys.exit(2)
