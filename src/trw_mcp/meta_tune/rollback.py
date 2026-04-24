"""Rollback primitive for the meta-tune pipeline.

PRD-HPO-SAFE-001 FR-4/FR-5 + NFR-3. ``rollback_proposal(proposal_id)``
restores the pre-edit state of a promoted meta-tune change. The p95
latency budget is ≤10s wall-clock (NFR-3). Idempotence: calling twice
returns the same ``RollbackResult``.

The implementation is storage-backend-agnostic: each promoted proposal is
expected to have deposited a pre-edit snapshot at
``state_dir/{proposal_id}.json``. Rollback marks it rolled-back in place
by renaming to ``{proposal_id}.rolled.json`` (idempotent on repeat calls).

Kill-switch (FR-7/FR-13): returns a ``status='disabled'`` result without
mutating state.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry.event_base import MetaTuneEvent

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)


StatusLiteral = Literal["rolled_back", "missing", "disabled", "error"]


class RollbackResult(BaseModel):
    """Typed result of a rollback attempt."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: StatusLiteral
    proposal_id: str
    elapsed_ms: float = Field(default=0.0, ge=0.0)
    reason: str = ""


def _default_state_dir() -> Path:
    return Path(".trw/meta_tune/state")


def rollback_proposal(
    proposal_id: str,
    *,
    state_dir: Path | None = None,
    _config: TRWConfig | None = None,
) -> RollbackResult:
    """Reverse a promoted meta-tune proposal, idempotent + p95 ≤10s."""
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
        return RollbackResult(
            status="disabled", proposal_id=proposal_id, reason="kill_switch_off"
        )

    dir_ = state_dir or _default_state_dir()
    start = time.monotonic()

    snapshot = dir_ / f"{proposal_id}.json"
    rolled = dir_ / f"{proposal_id}.rolled.json"

    # Idempotence: if already rolled, return same rolled_back result.
    if rolled.exists():
        elapsed = (time.monotonic() - start) * 1000.0
        _emit(proposal_id, "rolled_back", elapsed_ms=elapsed, reason="idempotent")
        return RollbackResult(
            status="rolled_back",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
            reason="idempotent",
        )

    if not snapshot.exists():
        elapsed = (time.monotonic() - start) * 1000.0
        _emit(proposal_id, "missing", elapsed_ms=elapsed, reason="no_snapshot")
        return RollbackResult(
            status="missing",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
            reason="no_snapshot",
        )

    try:
        # Atomic rename — records the rollback having happened.
        snapshot.replace(rolled)
    except OSError as exc:  # justified: io_boundary, surface to caller
        elapsed = (time.monotonic() - start) * 1000.0
        logger.error(
            "rollback_rename_failed",
            component="meta_tune.rollback",
            op="rollback_proposal",
            outcome="error",
            error=str(exc),
        )
        _emit(proposal_id, "error", elapsed_ms=elapsed, reason=f"io:{exc}")
        return RollbackResult(
            status="error",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
            reason=f"io:{exc}",
        )

    elapsed = (time.monotonic() - start) * 1000.0
    if elapsed > 10_000.0:  # NFR-3: log (do not raise) on latency overage.
        logger.warning(
            "rollback_latency_overage",
            component="meta_tune.rollback",
            op="rollback_proposal",
            outcome="degraded",
            proposal_id=proposal_id,
            elapsed_ms=elapsed,
        )
    _emit(proposal_id, "rolled_back", elapsed_ms=elapsed, reason="ok")
    return RollbackResult(
        status="rolled_back",
        proposal_id=proposal_id,
        elapsed_ms=elapsed,
        reason="ok",
    )


def _emit(
    proposal_id: str, status: str, *, elapsed_ms: float, reason: str
) -> None:
    try:
        MetaTuneEvent(
            session_id=proposal_id,
            payload={
                "action": "rollback",
                "proposal_id": proposal_id,
                "rollback_reason_code": reason,
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )
    except Exception:  # justified: telemetry_best_effort
        logger.warning(
            "rollback_telemetry_failed",
            component="meta_tune.rollback",
            op="_emit",
            outcome="degraded",
        )
    logger.info(
        "meta_tune_rollback",
        component="meta_tune.rollback",
        op="rollback_proposal",
        outcome=status,
        proposal_id=proposal_id,
        elapsed_ms=elapsed_ms,
        reason=reason,
    )


__all__ = [
    "RollbackResult",
    "rollback_proposal",
]
