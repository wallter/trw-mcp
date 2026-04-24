"""Tests for meta_tune.rollback — PRD-HPO-SAFE-001 FR-5 / NFR-3."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.meta_tune.rollback import (
    RollbackResult,
    rollback_proposal,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cfg(enabled: bool, audit_log_path: str, rollback_max_attempts: int = 1) -> TRWConfig:
    return TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=enabled,
            audit_log_path=audit_log_path,
            rollback_max_attempts=rollback_max_attempts,
        )
    )


def _write_snapshot(
    *,
    state_dir: Path,
    proposal_id: str,
    live_path: Path,
    backup_path: Path,
    promoted_at: datetime,
    promotion_session_id: str = "sess-1",
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{proposal_id}.json").write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "target_path": str(live_path),
                "backup_path": str(backup_path),
                "promotion_ts": promoted_at.isoformat(),
                "promotion_session_id": promotion_session_id,
            }
        )
    )


def test_rollback_noop_when_disabled(tmp_path: Path) -> None:
    r = rollback_proposal("p1", _config=_cfg(False, str(tmp_path / "audit.jsonl")))
    assert r.status == "disabled"
    assert r.proposal_id == "p1"


def test_rollback_returns_missing_when_no_state(tmp_path: Path) -> None:
    cfg = _cfg(True, str(tmp_path / "audit.jsonl"))
    r = rollback_proposal(
        "does-not-exist",
        state_dir=tmp_path,
        _config=cfg,
    )
    assert r.status == "missing"


def test_rollback_restores_pre_edit_state_and_audits(tmp_path: Path) -> None:
    audit_log = tmp_path / "audit.jsonl"
    cfg = _cfg(True, str(audit_log))
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    _write_snapshot(
        state_dir=state_dir,
        proposal_id="prop-1",
        live_path=live_file,
        backup_path=backup_file,
        promoted_at=datetime.now(timezone.utc),
    )

    r = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)

    assert r.status == "rolled_back"
    assert r.proposal_id == "prop-1"
    assert r.elapsed_ms >= 0
    assert live_file.read_text() == "original"
    assert audit_log.exists()
    assert "rolled_back" in audit_log.read_text()


def test_rollback_is_idempotent(tmp_path: Path) -> None:
    """FR-5: rollback(id) == rollback(id)."""
    cfg = _cfg(True, str(tmp_path / "audit.jsonl"))
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    _write_snapshot(
        state_dir=state_dir,
        proposal_id="prop-1",
        live_path=live_file,
        backup_path=backup_file,
        promoted_at=datetime.now(timezone.utc),
    )

    r1 = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)
    live_file.write_text("still-original")
    r2 = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)

    assert r1.status == "rolled_back"
    assert r2.status == "rolled_back"
    assert "idempotent" in r2.reason


def test_rollback_enforces_30_day_window(tmp_path: Path) -> None:
    cfg = _cfg(True, str(tmp_path / "audit.jsonl"))
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    _write_snapshot(
        state_dir=state_dir,
        proposal_id="prop-1",
        live_path=live_file,
        backup_path=backup_file,
        promoted_at=datetime.now(timezone.utc) - timedelta(days=31),
    )

    r = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)

    assert r.status == "window_expired"
    assert live_file.read_text() == "mutated"


def test_rollback_result_model_fields() -> None:
    r = RollbackResult(
        status="rolled_back",
        proposal_id="p",
        elapsed_ms=5.0,
        reason="ok",
    )
    assert r.status == "rolled_back"
    assert r.elapsed_ms == 5.0
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RollbackResult.model_validate(
            {"status": "rolled_back", "proposal_id": "p", "elapsed_ms": 0.0, "reason": "ok", "extra": 1}
        )


def test_rollback_completes_fast(tmp_path: Path) -> None:
    """NFR-3: rollback p95 ≤ 10s wall-clock (smoke threshold ≤1s)."""
    cfg = _cfg(True, str(tmp_path / "audit.jsonl"))
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    _write_snapshot(
        state_dir=state_dir,
        proposal_id="p1",
        live_path=live_file,
        backup_path=backup_file,
        promoted_at=datetime.now(timezone.utc),
    )
    r = rollback_proposal("p1", state_dir=state_dir, _config=cfg)
    assert r.elapsed_ms < 1000.0


def test_rollback_honors_max_attempts(tmp_path: Path) -> None:
    cfg = _cfg(True, str(tmp_path / "audit.jsonl"), rollback_max_attempts=1)
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "missing-backup.md"
    live_file.write_text("mutated")
    _write_snapshot(
        state_dir=state_dir,
        proposal_id="p1",
        live_path=live_file,
        backup_path=backup_file,
        promoted_at=datetime.now(timezone.utc),
    )

    first = rollback_proposal("p1", state_dir=state_dir, _config=cfg)
    second = rollback_proposal("p1", state_dir=state_dir, _config=cfg)

    assert first.status == "error"
    assert second.status == "error"
    assert second.reason == "rollback_attempt_limit_exceeded"
