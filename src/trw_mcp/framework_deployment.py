"""Staged canon generation deployment and rollback (INFRA-164 FR05/NFR05).

The final ``DEPLOYMENT.json`` replacement is the authoritative generation
pointer. Bodies and the human ``VERSION.yaml`` projection are staged and
verified first; readers reject any mixed/interrupted set whose receipt digests
do not match. A complete pre-deploy snapshot is retained for explicit rollback.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEPLOYMENT_RELATIVE_PATH = Path(".trw/frameworks/DEPLOYMENT.json")
_LOCK_RELATIVE_PATH = Path(".trw/frameworks/.deployment.lock")
_BACKUPS_RELATIVE_PATH = Path(".trw/frameworks/.rollback")
_STAGING_RELATIVE_PATH = Path(".trw/frameworks/.staging")
_RESERVED_ARTIFACT_PATHS = (
    DEPLOYMENT_RELATIVE_PATH,
    _LOCK_RELATIVE_PATH,
    _BACKUPS_RELATIVE_PATH,
    _STAGING_RELATIVE_PATH,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def _contained_path(target: Path, relative: Path) -> Path:
    """Resolve a managed path without following an existing symlink boundary."""
    candidate = target / relative
    current = candidate
    while current != target:
        if current.is_symlink():
            raise ValueError(f"deployment path crosses symlink: {relative}")
        current = current.parent
    try:
        candidate.resolve(strict=False).relative_to(target)
    except ValueError as exc:
        raise ValueError(f"deployment path escapes target: {relative}") from exc
    return candidate


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the host supports directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _replace_durable(source: Path, destination: Path) -> None:
    os.replace(source, destination)
    _fsync_directory(destination.parent)


@dataclass(frozen=True)
class DeploymentResult:
    generation_id: str
    rollback_id: str
    receipt_path: Path


@contextlib.contextmanager
def _deployment_lock(target: Path) -> Iterator[None]:
    lock_path = _contained_path(target, _LOCK_RELATIVE_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        os.chmod(lock_path, 0o600)
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback is process-local atomic receipt replacement
            pass
        yield


def _current_generation_id(target: Path) -> str:
    receipt = target / DEPLOYMENT_RELATIVE_PATH
    if receipt.is_file():
        try:
            value = json.loads(receipt.read_text(encoding="utf-8"))
            generation = value.get("generation_id") if isinstance(value, dict) else None
            if isinstance(generation, str) and generation:
                return generation
        except (OSError, json.JSONDecodeError):
            pass
    return "pre-deployment"


def _snapshot(target: Path, relative_paths: tuple[Path, ...], rollback_id: str) -> Path:
    backup = _contained_path(target, _BACKUPS_RELATIVE_PATH / rollback_id)
    backup.mkdir(parents=True, exist_ok=False)
    presence: dict[str, bool] = {}
    for relative in relative_paths:
        source = _contained_path(target, relative)
        key = relative.as_posix()
        presence[key] = source.is_file()
        if source.is_file():
            destination = backup / relative
            _write_durable(destination, source.read_bytes())
    _write_durable(backup / "BACKUP.json", _canonical_json({"schema_version": 1, "files": presence}))
    return backup


def _restore_snapshot(target: Path, backup: Path) -> None:
    metadata = json.loads((backup / "BACKUP.json").read_text(encoding="utf-8"))
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, dict):
        raise TypeError("rollback snapshot is malformed")
    # Receipt last: the restored generation is accepted only after every body
    # and projection has returned to its snapshotted bytes.
    ordered = sorted((Path(str(path)), bool(present)) for path, present in files.items())
    ordered.sort(key=lambda item: item[0] == DEPLOYMENT_RELATIVE_PATH)
    for relative, present in ordered:
        destination = _contained_path(target, relative)
        if not present:
            destination.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
            continue
        source = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=destination.parent, suffix=".rollback.tmp")
        os.close(fd)
        temp = Path(temp_name)
        try:
            _write_durable(temp, source.read_bytes())
            _replace_durable(temp, destination)
        finally:
            temp.unlink(missing_ok=True)


def deploy_framework_generation(
    target: Path,
    *,
    artifacts: Mapping[Path, bytes],
    registry_digest: str,
    framework_version: str,
    aaref_version: str,
    failure_after_promotions: int | None = None,
) -> DeploymentResult:
    """Stage, verify, and promote one receipt-bound complete generation."""
    target = target.resolve()
    normalized = {Path(path): bytes(data) for path, data in artifacts.items()}
    for relative in normalized:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"deployment path escapes target: {relative}")
        if any(relative == reserved or reserved in relative.parents for reserved in _RESERVED_ARTIFACT_PATHS):
            raise ValueError(f"deployment path is reserved: {relative}")
        _contained_path(target, relative)
    for managed in _RESERVED_ARTIFACT_PATHS:
        _contained_path(target, managed)
    artifact_digests = {path.as_posix(): _digest(data) for path, data in sorted(normalized.items())}
    generation_id = _digest(
        _canonical_json(
            {
                "registry_digest": registry_digest,
                "framework_version": framework_version,
                "aaref_version": aaref_version,
                "artifact_digests": artifact_digests,
            }
        )
    )
    deployed_at = datetime.now(timezone.utc).isoformat()
    receipt = _canonical_json(
        {
            "schema_version": 1,
            "generation_id": generation_id,
            "registry_digest": registry_digest,
            "framework_version": framework_version,
            "aaref_version": aaref_version,
            "artifact_digests": artifact_digests,
            "deployed_at": deployed_at,
        }
    )
    all_paths = (*normalized, DEPLOYMENT_RELATIVE_PATH)
    with _deployment_lock(target):
        prior = _current_generation_id(target)
        rollback_id = f"{prior}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        backup = _snapshot(target, all_paths, rollback_id)
        stage = _contained_path(target, _STAGING_RELATIVE_PATH / generation_id)
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            staged = {**normalized, DEPLOYMENT_RELATIVE_PATH: receipt}
            for relative, data in staged.items():
                path = stage / relative
                _write_durable(path, data)
                if _digest(path.read_bytes()) != _digest(data):
                    raise OSError(f"staged artifact verification failed: {relative}")

            # DEPLOYMENT.json is always last; it is the atomic acceptance pointer.
            for promoted, relative in enumerate(sorted(normalized, key=lambda path: path.as_posix()), start=1):
                destination = _contained_path(target, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                _replace_durable(stage / relative, destination)
                if failure_after_promotions is not None and promoted >= failure_after_promotions:
                    raise OSError("injected framework deployment failure")
            destination = _contained_path(target, DEPLOYMENT_RELATIVE_PATH)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _replace_durable(stage / DEPLOYMENT_RELATIVE_PATH, destination)
            return DeploymentResult(generation_id=generation_id, rollback_id=rollback_id, receipt_path=destination)
        except Exception:
            _restore_snapshot(target, backup)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def rollback_framework_generation(target: Path, rollback_id: str) -> None:
    """Restore a complete prior generation snapshot, receipt last."""
    target = target.resolve()
    backup = _contained_path(target, _BACKUPS_RELATIVE_PATH / rollback_id)
    if not backup.is_dir():
        raise FileNotFoundError(f"rollback generation not found: {rollback_id}")
    with _deployment_lock(target):
        _restore_snapshot(target, backup)


__all__ = [
    "DEPLOYMENT_RELATIVE_PATH",
    "DeploymentResult",
    "deploy_framework_generation",
    "rollback_framework_generation",
]
