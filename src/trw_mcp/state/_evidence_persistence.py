"""Durable, idempotent receipt persistence — PRD-CORE-205 FR09/NFR04.

State-layer persistence primitive. Re-exported through the
``tools/_evidence_receipts.py`` facade (and a thin
``tools/_evidence_persistence.py`` back-compat shim) for the tool layer, and
imported directly by ``state/_trust_receipts.py`` so the state layer never
imports from ``tools/`` (PRD-FIX-061-FR07 layer boundary).

Receipts are persisted per-run beneath ``meta/receipts/<type>/`` using atomic
same-filesystem replacement and canonical serialization. Because receipts are
run-scoped (not a single global path), a concurrent session cannot overwrite
another run's proof — the root cause of the 88c669bf4 build-status incident.

Guarantees:
- **Atomic**: temp-write + ``os.replace`` — a reader never sees a partial file.
- **Idempotent**: repeating an ID with byte-identical canonical payload is a
  no-op success; a different payload for the same ID fails ``receipt_id_collision``.
- **Bounded**: a canonical payload over 1 MiB fails before positive persistence.
- **Non-reusable**: a tombstoned ID is never re-minted or re-accepted.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from pydantic import BaseModel

from trw_mcp.models._evidence_core import EvidenceLimits, canonical_json
from trw_mcp.models._evidence_records import ReceiptTombstone

logger = structlog.get_logger(__name__)

_RETENTION_DAYS = 90
_TOMBSTONE_FILE = "_tombstones.jsonl"
# 128 bits of collision resistance per FR09 (16 bytes -> 32 hex chars).
_ID_ENTROPY_BYTES = 16


@dataclass(frozen=True)
class WriteOutcome:
    """Result of a receipt write attempt."""

    ok: bool
    receipt_id: str
    reason_code: str
    idempotent: bool = False


def canonical_receipt_bytes(model: BaseModel) -> bytes:
    """Deterministic canonical serialization of a receipt model."""
    return canonical_json(model.model_dump(mode="json"))


def generate_receipt_id(receipt_type: str) -> str:
    """Type-namespaced, 128-bit collision-resistant receipt ID."""
    return f"{receipt_type}-{secrets.token_hex(_ID_ENTROPY_BYTES)}"


def _receipts_root(run_path: Path) -> Path:
    return run_path / "meta" / "receipts"


def _receipt_path(run_path: Path, receipt_type: str, receipt_id: str) -> Path:
    return _receipts_root(run_path) / receipt_type / f"{receipt_id}.json"


def _tombstone_path(run_path: Path) -> Path:
    return _receipts_root(run_path) / _TOMBSTONE_FILE


def _is_tombstoned(run_path: Path, receipt_id: str) -> bool:
    path = _tombstone_path(run_path)
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if _safe_json_loads(line).get("receipt_id") == receipt_id:
                return True
    except OSError:
        return False
    return False


def _safe_json_loads(line: str) -> dict[str, object]:
    import json

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` atomically with owner-only perms."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-receipt-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # justified: chmod unsupported on some platforms; perms are best-effort
            pass
        os.replace(tmp_name, str(target))
    except BaseException:
        with _suppress_os_error():
            os.unlink(tmp_name)
        raise


class _suppress_os_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return isinstance(exc, OSError)


def write_receipt(
    run_path: Path,
    receipt_type: str,
    receipt_id: str,
    model: BaseModel,
) -> WriteOutcome:
    """Persist a receipt atomically with idempotency + collision + limit checks."""
    payload = canonical_receipt_bytes(model)
    if len(payload) > EvidenceLimits.MAX_CANONICAL_RECEIPT_BYTES:
        return WriteOutcome(False, receipt_id, "receipt_too_large")
    if _is_tombstoned(run_path, receipt_id):
        return WriteOutcome(False, receipt_id, "receipt_id_tombstoned")

    target = _receipt_path(run_path, receipt_type, receipt_id)
    existing = _read_bytes_or_none(target)
    if existing is not None:
        if existing == payload:
            return WriteOutcome(True, receipt_id, "idempotent", idempotent=True)
        logger.warning("receipt_id_collision", receipt_type=receipt_type, receipt_id=receipt_id)
        return WriteOutcome(False, receipt_id, "receipt_id_collision")

    try:
        _atomic_write_bytes(target, payload)
    except OSError:
        logger.warning("receipt_write_failed", receipt_type=receipt_type, exc_info=True)
        return WriteOutcome(False, receipt_id, "write_failed")
    return WriteOutcome(True, receipt_id, "written")


def _read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def read_receipt_bytes(run_path: Path, receipt_type: str, receipt_id: str) -> bytes | None:
    """Return the canonical bytes of a persisted receipt, or None when absent."""
    return _read_bytes_or_none(_receipt_path(run_path, receipt_type, receipt_id))


def list_receipt_ids(run_path: Path, receipt_type: str) -> list[str]:
    """List persisted receipt IDs for a type (sorted)."""
    directory = _receipts_root(run_path) / receipt_type
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def collect_receipts(
    run_path: Path,
    receipt_type: str,
    *,
    referenced_ids: frozenset[str],
    now: datetime | None = None,
    retention_days: int = _RETENTION_DAYS,
) -> list[str]:
    """GC expired, unreferenced receipts and leave tombstones (FR09).

    Only removes payloads that are BOTH older than the retention window AND not
    referenced. Referenced receipts survive regardless of age. Returns collected
    IDs. Each collected ID gets a tombstone so it can never be reused.
    """
    directory = _receipts_root(run_path) / receipt_type
    if not directory.exists():
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    collected: list[str] = []
    for path in sorted(directory.glob("*.json")):
        receipt_id = path.stem
        if receipt_id in referenced_ids:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime > cutoff:
            continue
        payload = _read_bytes_or_none(path)
        digest = "unknown"
        if payload is not None:
            import hashlib

            digest = hashlib.sha256(payload).hexdigest()
        _append_tombstone(run_path, receipt_type, receipt_id, digest, now)
        with _suppress_os_error():
            os.unlink(path)
        collected.append(receipt_id)
    return collected


def _append_tombstone(run_path: Path, receipt_type: str, receipt_id: str, digest: str, now: datetime) -> None:
    tombstone = ReceiptTombstone(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        canonical_digest=digest,
        collected_at=now.isoformat(),
    )
    path = _tombstone_path(run_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json(tombstone.model_dump(mode="json")).decode("utf-8") + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
