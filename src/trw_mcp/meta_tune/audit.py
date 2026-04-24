"""Hash-chained append-only audit log for SAFE-001."""

from __future__ import annotations

import hashlib
import json
import os
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


class AuditIntegrityError(RuntimeError):
    """Raised when an existing audit log is corrupted and append must stop."""


def _canonical(entry_sans_hash: dict[str, Any]) -> bytes:
    return json.dumps(entry_sans_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_entry_hash(prev_hash: str, entry_sans_hash: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(_canonical(entry_sans_hash))
    return h.hexdigest()


def _last_entry_hash(log_path: Path) -> str:
    broken_index = verify_audit_chain(log_path)
    if broken_index is not None:
        raise AuditIntegrityError(f"audit chain broken at row {broken_index}")
    if not log_path.exists():
        return _GENESIS_PREV
    last = ""
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                last = stripped
    if not last:
        return _GENESIS_PREV
    obj = json.loads(last)
    value = obj.get("entry_hash", _GENESIS_PREV)
    return str(value) if isinstance(value, str) else _GENESIS_PREV


def append_audit_entry(
    log_path: Path,
    *,
    edit_id: str | None = None,
    proposal_id: str | None = None,
    event: str = "recorded",
    proposer_id: str = "",
    candidate_diff: str = "",
    surface_classification: str,
    gate_decision: str,
    payload: dict[str, Any],
    promotion_session_id: str = "",
    reviewer_id: str | None = None,
    vote_type: str | None = None,
    verdict: str | None = None,
    voter_id: str | None = None,
    ts: datetime | None = None,
    _config: TRWConfig | None = None,
) -> dict[str, Any] | None:
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

    resolved_edit_id = edit_id or proposal_id
    if not resolved_edit_id:
        raise ValueError("append_audit_entry requires edit_id or proposal_id")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = _last_entry_hash(log_path)
    entry_sans_hash: dict[str, Any] = {
        "ts": (ts or datetime.now(tz=timezone.utc)).isoformat(),
        "edit_id": resolved_edit_id,
        "event": event,
        "proposer_id": proposer_id,
        "candidate_diff": candidate_diff,
        "surface_classification": surface_classification,
        "gate_decision": gate_decision,
        "promotion_session_id": promotion_session_id,
        "reviewer_id": reviewer_id,
        "vote_type": vote_type,
        "verdict": verdict,
        "voter_id": voter_id,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    entry_hash = _compute_entry_hash(prev_hash, entry_sans_hash)
    entry = {**entry_sans_hash, "entry_hash": entry_hash}
    line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"

    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
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
        edit_id=resolved_edit_id,
        audit_event=event,
    )
    return entry


def verify_audit_chain(log_path: Path) -> int | None:
    if not log_path.exists():
        return None
    try:
        content = log_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    rows = [line for line in content.splitlines() if line.strip()]
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
        if _compute_entry_hash(prev, entry_sans_hash) != stated_hash:
            return idx
        prev = stated_hash
    return None


__all__ = [
    "AuditAppendError",
    "AuditIntegrityError",
    "append_audit_entry",
    "verify_audit_chain",
]
