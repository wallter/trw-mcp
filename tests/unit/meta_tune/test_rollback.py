"""Tests for meta_tune.rollback — PRD-HPO-SAFE-001 FR-4/FR-5."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.meta_tune.rollback import (
    RollbackResult,
    rollback_proposal,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cfg(enabled: bool, tmp: Path) -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=enabled))


def test_rollback_noop_when_disabled(tmp_path: Path) -> None:
    r = rollback_proposal("p1")
    assert r.status == "disabled"
    assert r.proposal_id == "p1"


def test_rollback_returns_missing_when_no_state(tmp_path: Path) -> None:
    cfg = _cfg(True, tmp_path)
    r = rollback_proposal(
        "does-not-exist",
        state_dir=tmp_path,
        _config=cfg,
    )
    assert r.status == "missing"


def test_rollback_restores_pre_edit_state(tmp_path: Path) -> None:
    cfg = _cfg(True, tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Simulate a stored pre-edit snapshot.
    snapshot = state_dir / "prop-1.json"
    snapshot.write_text('{"proposal_id": "prop-1", "restored_surface": "CLAUDE.md"}')
    r = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)
    assert r.status == "rolled_back"
    assert r.proposal_id == "prop-1"
    assert r.elapsed_ms >= 0


def test_rollback_is_idempotent(tmp_path: Path) -> None:
    """FR-5: rollback(id) == rollback(id)."""
    cfg = _cfg(True, tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    snapshot = state_dir / "prop-1.json"
    snapshot.write_text('{"proposal_id": "prop-1"}')
    r1 = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)
    r2 = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)
    assert r1.status == "rolled_back"
    # Second call finds the already-rolled-back marker and returns same status.
    assert r2.status == "rolled_back"


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
            {"status": "rolled_back", "proposal_id": "p", "elapsed_ms": 0.0,
             "reason": "ok", "extra": 1}
        )


def test_rollback_completes_fast(tmp_path: Path) -> None:
    """NFR-3: rollback p95 ≤ 10s wall-clock (smoke threshold ≤1s)."""
    cfg = _cfg(True, tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "p1.json").write_text('{}')
    r = rollback_proposal("p1", state_dir=state_dir, _config=cfg)
    assert r.elapsed_ms < 1000.0
