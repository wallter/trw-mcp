"""Named execution-artifact freshness for CORE-205 FR06 receipt consumers.

Shared by evidence gates and trust readers; deliberately separate from content
bindings, whose symlink entries bind link text rather than execution bytes.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from pathlib import Path

from trw_mcp.models._evidence_core import EvidenceLimits, ReceiptState
from trw_mcp.models._evidence_records import VerificationReceipt
from trw_mcp.state._evidence_binding import BindingOutcome

_CHUNK_BYTES = 1024 * 1024


def _safe_read_supported() -> bool:
    return (
        all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (*_identity(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _result(state: ReceiptState, reason: str) -> BindingOutcome:
    return BindingOutcome(None, state, reason)


def _read_digest(fd: int, expected_size: int) -> str | None:
    """Read at most the allowed bound plus one byte; reject growth/short reads."""
    digest = hashlib.sha256()
    total = 0
    while total <= expected_size:
        chunk = os.read(fd, min(_CHUNK_BYTES, expected_size + 1 - total))
        if not chunk:
            return digest.hexdigest() if total == expected_size else None
        total += len(chunk)
        digest.update(chunk)
    return None


def _check_artifact(root: Path, parts: list[str], expected: str) -> BindingOutcome:
    descriptors: list[int] = []
    links: list[tuple[int, str, int]] = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        parent = root_fd
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=parent)
            descriptors.append(child)
            links.append((parent, part, child))
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return _result(ReceiptState.INVALID, "artifact_not_regular")
        if before.st_size > EvidenceLimits.MAX_BOUND_FILE_BYTES:
            return _result(ReceiptState.INVALID, "artifact_too_large")
        digest = _read_digest(fd, before.st_size)
        after = os.fstat(fd)
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if digest is None or _signature(before) != _signature(after) or _signature(after) != _signature(named):
            return _result(ReceiptState.UNSTABLE_READ, "artifact_unstable_read")
        # Verify every opened directory still belongs to the named root chain.
        # Descriptor-relative traversal never follows a substituted parent link.
        for parent_fd, name, child_fd in reversed(links):
            named_directory = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _identity(named_directory) != _identity(os.fstat(child_fd)):
                return _result(ReceiptState.UNSTABLE_READ, "artifact_path_changed")
        if _identity(os.stat(root, follow_symlinks=False)) != _identity(os.fstat(root_fd)):
            return _result(ReceiptState.UNSTABLE_READ, "artifact_path_changed")
        # T16: os.stat's mtime/ctime resolution is coarse on some filesystems
        # (~1ms on Linux ext4/tmpfs; hidden on macOS APFS's true nanoseconds), so
        # a rewrite-then-restore-mtime that completes within one clock tick can
        # leave _signature(before) == _signature(after) == _signature(named) even
        # though the bytes changed underneath the read. A second independent read
        # of the same descriptor is not subject to clock resolution at all: any
        # content mutation between the two reads changes the digest regardless of
        # how coarse the filesystem's timestamps are. Placed after the identity
        # checks above so a path/parent/root swap (already content-stable at the
        # byte level) is still reported as the more specific artifact_path_changed.
        os.lseek(fd, 0, os.SEEK_SET)
        reread_digest = _read_digest(fd, before.st_size)
        if reread_digest is None or reread_digest != digest:
            return _result(ReceiptState.UNSTABLE_READ, "artifact_unstable_read")
        if digest != expected:
            return _result(ReceiptState.STALE_CONTENT, "artifact_content_changed")
        return _result(ReceiptState.VALID, "artifact_current")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verification_artifact_is_current(receipt: VerificationReceipt, project_root: Path) -> BindingOutcome:
    """Validate named raw bytes, without authenticating their claimed execution.

    Legacy receipts remain parseable but never establish artifact evidence.
    Unsupported safe filesystem capabilities fail closed, with no path fallback.
    The result is a bounded observation, not a lock against subsequent writes.
    """
    path = receipt.evidence_artifact_path
    expected = receipt.evidence_artifact_digest
    if not path or not expected:
        return _result(ReceiptState.LEGACY_UNBOUND, "artifact_unbound")
    try:
        parts = path.split("/")
        if (
            len(path.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES
            or any(part in {"", ".", ".."} for part in parts)
            or any(character in path for character in ("\\", ":", "\x00"))
        ):
            return _result(ReceiptState.INVALID, "artifact_path_invalid")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            return _result(ReceiptState.INVALID, "artifact_digest_invalid")
        if not _safe_read_supported():
            return _result(ReceiptState.DEGRADED, "artifact_safe_read_unavailable")
        return _check_artifact(project_root.resolve(strict=True), parts, expected)
    except NotImplementedError:
        return _result(ReceiptState.DEGRADED, "artifact_safe_read_unavailable")
    except FileNotFoundError:
        return _result(ReceiptState.MISSING, "artifact_missing")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            return _result(ReceiptState.INVALID, "artifact_path_unsafe")
        return _result(ReceiptState.DEGRADED, "artifact_read_error")
    except (ValueError, RuntimeError, UnicodeError):
        return _result(ReceiptState.INVALID, "artifact_path_invalid")
