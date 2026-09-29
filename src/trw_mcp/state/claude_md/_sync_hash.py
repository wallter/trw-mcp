"""CLAUDE.md sync-cache hashing — stable digest + stored-hash read/write/invalidate.

Belongs to the ``_sync.py`` facade. Split out so ``_sync`` stays under the
``state/claude_md`` 350-line gate. Public symbols (``_compute_sync_hash``,
``_read_stored_hash``, ``_write_stored_hash``, ``invalidate_claude_md_hash``)
are re-exported through ``_sync`` — ``_profile_dispatcher`` and the tests import
them from ``_sync`` unchanged.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from trw_mcp._checkout_write import write_checkout_file

logger = structlog.get_logger(__name__)

# FR04 (PRD-FIX-053): Hash file name within .trw/context/
_HASH_FILE_NAME = "claude_md_hash.txt"


def _compute_sync_hash() -> str:
    """Compute a stable SHA-256 hash of the sync inputs.

    PRD-CORE-093 FR05: Hash excludes learning content — only template version
    (via package version) determines whether CLAUDE.md needs re-rendering.
    This ensures consecutive trw_deliver calls produce identical CLAUDE.md.

    Returns:
        64-character hex SHA-256 digest.
    """
    from importlib.metadata import PackageNotFoundError, version

    h = hashlib.sha256()

    # Package version — invalidates cache on any trw-mcp upgrade
    try:
        pkg_version = version("trw-mcp")
    except PackageNotFoundError:
        pkg_version = "unknown"
        logger.warning("claude_md_hash_version_unknown")
    h.update(pkg_version.encode("utf-8"))
    h.update(b"\x00")

    return h.hexdigest()


def _hash_file_path(trw_dir: Path) -> Path:
    """Return the hash file path."""
    return trw_dir / "context" / _HASH_FILE_NAME


def _read_stored_hash(trw_dir: Path) -> str | None:
    """Read the stored hash from .trw/context/claude_md_hash.txt."""
    hash_file = _hash_file_path(trw_dir)
    try:
        return hash_file.read_text(encoding="utf-8").strip() if hash_file.exists() else None
    except OSError:
        return None


def _write_stored_hash(trw_dir: Path, digest: str) -> None:
    """Write the hash to .trw/context/claude_md_hash.txt, anchored on *trw_dir*.

    A symlink planted at the hash file or at ``context/`` raises ``UnsafeWriteError`` (PRD-CORE-337 FR08);
    it is not an ``OSError``, so it propagates to the sync instead of being logged away below.
    """
    hash_file = _hash_file_path(trw_dir)
    try:
        trw_dir.mkdir(parents=True, exist_ok=True)
        write_checkout_file(trw_dir, hash_file, digest)
    except OSError:
        logger.debug("claude_md_hash_write_failed", path=str(hash_file))
