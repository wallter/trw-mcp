"""Opaque filesystem-root identity shared by binding and commit-provenance readers.

Identity is location-bound, not a portable repository ID or authentication.
Native resolved path bytes are hashed without case folding or disclosure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from trw_mcp.models._evidence_core import ReceiptState

_PREFIX = "root-v1:sha256:"
_DOMAIN = b"trw.core205.project-root.v1\x00"


class ProjectIdentityError(RuntimeError):
    """Root identity could not be observed safely and consistently."""


def resolve_project_identity(project_root: Path) -> str:
    """Hash canonical native root bytes and device/inode after stable observation."""
    fd: int | None = None
    try:
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise ProjectIdentityError("project_identity_unavailable")
        root = project_root.resolve(strict=True)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(fd)
        named = os.stat(root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or project_root.resolve(strict=True) != root
        ):
            raise ProjectIdentityError("project_identity_changed")
        alias = os.stat(project_root)
        if (alias.st_dev, alias.st_ino) != (opened.st_dev, opened.st_ino):
            raise ProjectIdentityError("project_identity_changed")
        payload = json.dumps(
            {"path_bytes_hex": os.fsencode(root).hex(), "device": opened.st_dev, "inode": opened.st_ino},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return _PREFIX + hashlib.sha256(_DOMAIN + payload).hexdigest()
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, ProjectIdentityError):
            raise
        raise ProjectIdentityError("project_identity_unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)


def project_identity_is_current(recorded: str, project_root: Path) -> tuple[ReceiptState, str]:
    """Legacy strings remain readable, but only a current v1 identity is positive."""
    if not recorded or re.fullmatch(re.escape(_PREFIX) + r"[0-9a-f]{64}", recorded) is None:
        legacy_basename = bool(recorded) and not any(char in recorded for char in (":", "/", "\\", "\x00"))
        if legacy_basename:
            return ReceiptState.LEGACY_UNBOUND, "project_identity_legacy"
        return ReceiptState.INVALID, "project_identity_invalid"
    try:
        current = resolve_project_identity(project_root)
    except ProjectIdentityError:
        return ReceiptState.DEGRADED, "project_identity_unavailable"
    if recorded != current:
        return ReceiptState.INVALID, "project_identity_mismatch"
    return ReceiptState.VALID, "ok"
