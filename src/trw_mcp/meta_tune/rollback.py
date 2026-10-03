"""Rollback primitive for SAFE-001."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file
from trw_mcp.meta_tune.audit import append_audit_entry
from trw_mcp.meta_tune.promote_helpers import resolve_repo

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

StatusLiteral = Literal["rolled_back", "missing", "disabled", "error", "window_expired"]


class RollbackResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: StatusLiteral
    proposal_id: str
    elapsed_ms: float = Field(default=0.0, ge=0.0)
    reason: str = ""


def _default_state_dir() -> Path:
    return Path(".trw/meta_tune/state")


def _load_snapshot(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("rollback snapshot must be a JSON object")
    required = {"target_path", "backup_path", "promotion_ts"}
    missing = required - set(raw)
    if missing:
        raise ValueError(f"rollback snapshot missing keys: {sorted(missing)}")
    return {k: str(v) for k, v in raw.items()}


def rollback_proposal(
    proposal_id: str,
    *,
    state_dir: Path | None = None,
    _config: TRWConfig | None = None,
) -> RollbackResult:
    cfg = _config
    if cfg is None:
        from trw_mcp.models.config._main import TRWConfig

        cfg = TRWConfig()
    if not cfg.meta_tune.enabled:
        logger.warning(
            "meta_tune_disabled",
            component="meta_tune.rollback",
            op="rollback_proposal",
            outcome="noop",
            reason="kill_switch_off",
        )
        return RollbackResult(status="disabled", proposal_id=proposal_id, reason="kill_switch_off")

    dir_ = state_dir or _default_state_dir()
    start = time.monotonic()
    snapshot_path = dir_ / f"{proposal_id}.json"
    rolled_path = dir_ / f"{proposal_id}.rolled.json"

    if rolled_path.exists():
        elapsed = (time.monotonic() - start) * 1000.0
        return RollbackResult(
            status="rolled_back",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
            reason="idempotent",
        )
    if not snapshot_path.exists():
        elapsed = (time.monotonic() - start) * 1000.0
        return RollbackResult(status="missing", proposal_id=proposal_id, elapsed_ms=elapsed, reason="no_snapshot")

    try:
        snapshot = _load_snapshot(snapshot_path)
        attempts = int(snapshot.get("rollback_attempts", "0"))
        if attempts >= cfg.meta_tune.rollback_max_attempts:
            elapsed = (time.monotonic() - start) * 1000.0
            return RollbackResult(
                status="error",
                proposal_id=proposal_id,
                elapsed_ms=elapsed,
                reason="rollback_attempt_limit_exceeded",
            )
        promoted_at = datetime.fromisoformat(snapshot["promotion_ts"])
        if promoted_at.tzinfo is None:
            promoted_at = promoted_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - promoted_at > timedelta(days=30):
            elapsed = (time.monotonic() - start) * 1000.0
            return RollbackResult(
                status="window_expired",
                proposal_id=proposal_id,
                elapsed_ms=elapsed,
                reason="rollback_window_expired",
            )
        target_path = Path(snapshot["target_path"])
        backup_path = Path(snapshot["backup_path"])
        # Resolve the directory, not the leaf: a symlink AT the target is refused by the write, never followed
        # (CORE-337-D; was shutil.copy2). The backup's mode bits travel with its bytes, as copy2 carried them.
        resolved_target = target_path.parent.resolve() / target_path.name
        write_checkout_file(
            resolve_repo(resolved_target).resolve(),
            resolved_target,
            backup_path.read_bytes(),
            mode=backup_path.stat().st_mode & 0o7777,
        )
        snapshot_path.replace(rolled_path)
        elapsed = (time.monotonic() - start) * 1000.0
        append_audit_entry(
            Path(cfg.meta_tune.audit_log_path),
            edit_id=proposal_id,
            event="rolled_back",
            proposer_id="operator",
            candidate_diff="",
            surface_classification="advisory",
            gate_decision="rolled_back",
            promotion_session_id=snapshot.get("promotion_session_id", ""),
            payload={"target_path": str(target_path), "backup_path": str(backup_path)},
            _config=cfg,
        )
    except (OSError, ValueError, json.JSONDecodeError, UnsafeWriteError) as exc:
        try:
            snapshot_data: Any = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(snapshot_data, dict):
                snapshot_data["rollback_attempts"] = int(snapshot_data.get("rollback_attempts", 0)) + 1
                state_root = dir_.resolve()
                write_checkout_file(state_root, state_root / snapshot_path.name, json.dumps(snapshot_data))
        except Exception as attempts_exc:
            logger.warning(
                "rollback_attempt_counter_update_failed",
                component="meta_tune.rollback",
                op="rollback_proposal",
                outcome="degraded",
                error=str(attempts_exc),
            )
        elapsed = (time.monotonic() - start) * 1000.0
        logger.exception(
            "rollback_failed",
            component="meta_tune.rollback",
            op="rollback_proposal",
            outcome="error",
            error=str(exc),
        )
        return RollbackResult(status="error", proposal_id=proposal_id, elapsed_ms=elapsed, reason=str(exc))

    if elapsed > 10_000.0:
        logger.warning(
            "rollback_latency_overage",
            component="meta_tune.rollback",
            op="rollback_proposal",
            outcome="degraded",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
        )
    logger.info(
        "meta_tune_rollback",
        component="meta_tune.rollback",
        op="rollback_proposal",
        outcome="rolled_back",
        proposal_id=proposal_id,
        elapsed_ms=elapsed,
    )
    return RollbackResult(status="rolled_back", proposal_id=proposal_id, elapsed_ms=elapsed, reason="ok")


__all__ = [
    "RollbackResult",
    "rollback_proposal",
]
