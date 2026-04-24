"""Rollback primitive for SAFE-001."""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.meta_tune.audit import append_audit_entry

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
        raise ValueError("rollback snapshot must be a JSON object")
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
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, target_path)
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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(snapshot, dict):
                snapshot["rollback_attempts"] = int(snapshot.get("rollback_attempts", 0)) + 1
                snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        except Exception:
            pass
        elapsed = (time.monotonic() - start) * 1000.0
        logger.error(
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
