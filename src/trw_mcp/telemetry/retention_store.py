"""Content-addressed immutable retention store (PRD-CORE-181-FR03).

One immutable payload per digest under ``.trw/retention/store/<aa>/<digest>``;
repeated snapshots/receipts reference the canonical blob instead of copying
it. Every uncertain case — collision (same digest, different bytes), unstable
read, partial write, missing reference target, authority lookup failure — is
NON-DESTRUCTIVE and blocks collection. This store never alters
``SurfaceRegistry`` snapshot semantics (it does not import it).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from enum import Enum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger(__name__)

STORE_RELATIVE_DIR = Path(".trw") / "retention" / "store"
REFS_RELATIVE_DIR = Path(".trw") / "retention" / "refs"


class StoreOutcome(str, Enum):
    STORED = "stored"  # new canonical blob written
    DEDUPLICATED = "deduplicated"  # identical bytes already canonical
    COLLISION_BLOCKED = "collision_blocked"  # same digest, different bytes — retain both, no write
    UNSTABLE_READ = "unstable_read"  # source changed while reading — nothing written


class StoreReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    digest: str
    outcome: StoreOutcome
    blob_path: str = ""
    collectible: bool = False  # only a resolved, unreferenced blob may collect later


def _blob_path(root: Path, digest_hex: str) -> Path:
    return root / STORE_RELATIVE_DIR / digest_hex[:2] / digest_hex


def store_payload(root: Path, payload: bytes) -> StoreReceipt:
    """Store bytes content-addressed; dedupe on identical bytes; block on collision."""
    digest_hex = hashlib.sha256(payload).hexdigest()
    digest = "sha256:" + digest_hex
    target = _blob_path(root, digest_hex)
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError:
            logger.warning("retention_blob_unreadable", digest=digest)
            return StoreReceipt(digest=digest, outcome=StoreOutcome.COLLISION_BLOCKED)
        if existing == payload:
            return StoreReceipt(
                digest=digest,
                outcome=StoreOutcome.DEDUPLICATED,
                blob_path=str(target),
                collectible=False,
            )
        # Same digest, different bytes: impossible for honest SHA-256 inputs,
        # so treat as tampering/corruption — retain BOTH, write nothing.
        logger.warning("retention_store_collision", digest=digest)
        return StoreReceipt(digest=digest, outcome=StoreOutcome.COLLISION_BLOCKED)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".blob.tmp")
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        tmp.write_bytes(payload)
        os.chmod(tmp, 0o444)  # immutable-by-convention: read-only blob
        tmp.replace(target)
    except Exception:  # justified: partial write must leave no canonical blob behind
        tmp.unlink(missing_ok=True)
        raise
    return StoreReceipt(digest=digest, outcome=StoreOutcome.STORED, blob_path=str(target), collectible=False)


def store_file(root: Path, source: Path) -> StoreReceipt:
    """Stable-read a file into the store; a mid-read change stores NOTHING."""
    try:
        before = source.stat()
        payload = source.read_bytes()
        after = source.stat()
    except OSError:
        return StoreReceipt(digest="", outcome=StoreOutcome.UNSTABLE_READ)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return StoreReceipt(digest="", outcome=StoreOutcome.UNSTABLE_READ)
    return store_payload(root, payload)


def add_reference(root: Path, digest: str, reference_id: str) -> Path:
    """Register an inbound reference; a referenced blob is never collectible."""
    refs_dir = root / REFS_RELATIVE_DIR / digest.removeprefix("sha256:")
    refs_dir.mkdir(parents=True, exist_ok=True)
    ref_file = refs_dir / f"{reference_id}.json"
    ref_file.write_text(json.dumps({"digest": digest, "reference_id": reference_id}) + "\n")
    return ref_file


def resolve_reference(root: Path, digest: str) -> Path | None:
    """Return the canonical blob for a digest, or None (typed absence)."""
    target = _blob_path(root, digest.removeprefix("sha256:"))
    if not target.is_file():
        return None
    try:
        actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return None
    return target if actual == digest else None


def is_collectible(root: Path, digest: str) -> tuple[bool, str]:
    """A blob collects ONLY when it resolves cleanly and has zero references.

    Any uncertainty (missing blob, digest mismatch, unreadable refs dir)
    returns ``(False, reason)`` — fail closed, never destructive.
    """
    blob = resolve_reference(root, digest)
    if blob is None:
        return False, "blob_missing_or_corrupt"
    refs_dir = root / REFS_RELATIVE_DIR / digest.removeprefix("sha256:")
    try:
        if refs_dir.is_dir() and any(refs_dir.iterdir()):
            return False, "blob_referenced"
    except OSError:
        return False, "references_unreadable"
    return True, ""
