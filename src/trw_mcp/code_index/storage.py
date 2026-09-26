"""Atomic JSON storage for the local code-index manifest."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from pydantic import ValidationError

from trw_mcp.code_index.models import CodeIndexManifest

MANIFEST_RELATIVE_PATH: str = ".trw/code-index/manifest.json"
#: About 200 bytes a row: fifty times the 50,000-file build bound's manifest would still fit.
MAX_MANIFEST_BYTES: int = 64 * 1024 * 1024


def default_manifest_path(repo_root: Path | str) -> Path:
    """Return the canonical manifest path for ``repo_root``."""

    return Path(repo_root) / MANIFEST_RELATIVE_PATH


def load_manifest(path: Path) -> CodeIndexManifest | None:
    """Load a manifest, returning ``None`` for missing or corrupt state."""

    try:  # the checkout's file: never through a symlink, never a FIFO, never unbounded (rc8 pre-C12)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:  # trw-fail-silent-allow: no readable previous manifest only means nothing is reused; the build rehashes every file
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(fd)
        data = handle.read(MAX_MANIFEST_BYTES + 1) if stat.S_ISREG(info.st_mode) else b""
    if not data or len(data) > MAX_MANIFEST_BYTES:
        return None
    try:
        return CodeIndexManifest.model_validate_json(data)
    except ValidationError:  # trw-fail-silent-allow: a corrupt or crafted previous manifest is treated as absent; the build rehashes every file
        return None


def ensure_index_dir(repo_root: Path) -> Path:
    """Create or reuse ``.trw/code-index`` under *repo_root*, refusing a symlink or non-directory at either level.

    The build writes into the checkout, whose content is not trusted: a committed ``.trw`` or
    ``.trw/code-index`` symlink would redirect the store and the manifest (rc8 pre-C12 sol review).
    """
    current = Path(repo_root)
    for part in (".trw", "code-index"):
        current = current / part
        if current.is_symlink() or (os.path.lexists(current) and not current.is_dir()):
            raise ValueError(f"refusing to write the code index through {current}: it is not a real directory")
        current.mkdir(exist_ok=True)
    return current


def save_manifest(path: Path, manifest: CodeIndexManifest) -> None:
    """Persist ``manifest`` via a fresh temp file (never a planted one) plus atomic replace."""

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(manifest_text(manifest))
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def manifest_text(manifest: CodeIndexManifest) -> str:
    """The manifest as saved; the build refuses one over :data:`MAX_MANIFEST_BYTES` before publishing anything."""
    return f"{manifest.model_dump_json(indent=2)}\n"


__all__ = [
    "MANIFEST_RELATIVE_PATH",
    "MAX_MANIFEST_BYTES",
    "default_manifest_path",
    "ensure_index_dir",
    "load_manifest",
    "manifest_text",
    "save_manifest",
]
