"""Hash-chained append-only audit log for the meta-tune pipeline.

PRD-HPO-SAFE-001 FR-3/FR-4 (audit trail) + FR-14 (incremental persistence)
+ FR-16 (promotion_session_id isolation) + NFR-2 (immutability).

Every lifecycle event for a meta-tune proposal appends a JSONL row with:

    {ts, proposal_id, surface_classification, gate_decision, payload,
     promotion_session_id, prev_hash, entry_hash}

where ``entry_hash = sha256(prev_hash || canonical_json(entry_sans_hash))``.
The genesis entry uses ``prev_hash = "0" * 64``. Tampering with any row
breaks ``verify_audit_chain()`` at the first invalid index.

FR-7/FR-13 kill-switch: when ``config.meta_tune.enabled`` is False the
append becomes a no-op with a WARN log. Callers receive ``None``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

_GENESIS_PREV: str = "0" * 64


class AuditAppendError(RuntimeError):
    """Raised when an audit append cannot reach durable storage."""


def _canonical(entry_sans_hash: dict[str, Any]) -> bytes:
    """Canonical JSON bytes for hashing — sorted keys, no whitespace."""
    return json.dumps(entry_sans_hash, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _compute_entry_hash(prev_hash: str, entry_sans_hash: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(_canonical(entry_sans_hash))
    return h.hexdigest()


def _last_entry_hash(log_path: Path) -> str:
    """Return the entry_hash of the last row, or the genesis prev hash."""
    if not log_path.exists():
        return _GENESIS_PREV
    last = ""
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
    except OSError as exc:  # justified: io_boundary, audit must not crash caller
        logger.error(
            "audit_read_tail_failed",
            component="meta_tune.audit",
            op="_last_entry_hash",
            outcome="error",
            error=str(exc),
        )
        return _GENESIS_PREV
    if not last:
        return _GENESIS_PREV
    try:
        obj = json.loads(last)
    except json.JSONDecodeError:  # justified: corrupted_tail, degrade to genesis
        return _GENESIS_PREV
    val = obj.get("entry_hash", _GENESIS_PREV)
    return str(val) if isinstance(val, str) else _GENESIS_PREV


def append_audit_entry(
    log_path: Path,
    *,
    proposal_id: str,
    surface_classification: str,
    gate_decision: str,
    payload: dict[str, Any],
    promotion_session_id: str = "",
    ts: datetime | None = None,
    _config: TRWConfig | None = None,
) -> dict[str, Any] | None:
    """Append a single hash-chained audit row.

    Returns the written entry dict, or ``None`` when the kill switch is
    active (FR-7/FR-13).
    """
    cfg = _config
    if cfg is None:
        from trw_mcp.models.config._main import TRWConfig

        cfg = TRWConfig()
    if not cfg.meta_tune.enabled:
        logger.warning(
            "meta_tune_disabled",
            component="meta_tune.audit",
            op="append_audit_entry",
            outcome="noop",
            reason="kill_switch_off",
        )
        return None

    log_path.parent.mkdir(parents=True, exist_ok=True)

    prev_hash = _last_entry_hash(log_path)
    entry_sans_hash: dict[str, Any] = {
        "ts": (ts or datetime.now(tz=timezone.utc)).isoformat(),
        "proposal_id": proposal_id,
        "surface_classification": surface_classification,
        "gate_decision": gate_decision,
        "promotion_session_id": promotion_session_id,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    entry_hash = _compute_entry_hash(prev_hash, entry_sans_hash)
    entry: dict[str, Any] = {**entry_sans_hash, "entry_hash": entry_hash}

    # FR-14: incremental persistence — flush + fsync the single row now.
    line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:  # justified: portability, some filesystems no fsync
                pass
    except OSError as exc:
        logger.error(
            "audit_append_failed",
            component="meta_tune.audit",
            op="append_audit_entry",
            outcome="error",
            error=str(exc),
        )
        raise AuditAppendError(str(exc)) from exc

    logger.info(
        "meta_tune_audit_append",
        component="meta_tune.audit",
        op="append_audit_entry",
        outcome="ok",
        proposal_id=proposal_id,
        gate_decision=gate_decision,
        surface_classification=surface_classification,
    )
    return entry


def verify_audit_chain(log_path: Path) -> int | None:
    """Walk the chain and return the first broken index, or ``None`` if intact.

    Empty/missing files are treated as intact (nothing to verify yet).
    """
    if not log_path.exists():
        return None
    try:
        content = log_path.read_text(encoding="utf-8")
    except OSError as exc:  # justified: io_boundary
        logger.error(
            "audit_verify_read_failed",
            component="meta_tune.audit",
            op="verify_audit_chain",
            outcome="error",
            error=str(exc),
        )
        return 0
    rows = [ln for ln in content.splitlines() if ln.strip()]
    if not rows:
        return None

    prev = _GENESIS_PREV
    for idx, raw in enumerate(rows):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return idx
        if not isinstance(obj, dict):
            return idx
        stated_prev = obj.get("prev_hash")
        stated_hash = obj.get("entry_hash")
        if stated_prev != prev or not isinstance(stated_hash, str):
            return idx
        entry_sans_hash = {k: v for k, v in obj.items() if k != "entry_hash"}
        expected = _compute_entry_hash(prev, entry_sans_hash)
        if expected != stated_hash:
            return idx
        prev = stated_hash
    return None


__all__ = [
    "AuditAppendError",
    "append_audit_entry",
    "verify_audit_chain",
]
