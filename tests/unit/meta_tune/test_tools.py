"""Tests for SAFE-001 MCP tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, get_tools_sync, make_test_server


def test_registers_surface_classify_and_rollback_tools() -> None:
    server = make_test_server("meta_tune")
    tools = get_tools_sync(server)

    assert "trw_meta_tune_propose" in tools
    assert "trw_surface_classify" in tools
    assert "trw_meta_tune_rollback" in tools


def test_surface_classify_tool_reports_control_for_trw_config(tmp_path: Path) -> None:
    server = make_test_server("meta_tune")
    classify = extract_tool_fn(server, "trw_surface_classify")

    result = classify(".trw/config.yaml")

    assert result["classification"] == "control"
    assert "config" in result["surfaces"]


def test_rollback_tool_restores_target_file(tmp_path: Path) -> None:
    server = make_test_server("meta_tune")
    rollback = extract_tool_fn(server, "trw_meta_tune_rollback")
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    audit_log = tmp_path / "audit.jsonl"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    state_dir.mkdir()
    (state_dir / "prop-1.json").write_text(
        json.dumps(
            {
                "proposal_id": "prop-1",
                "target_path": str(live_file),
                "backup_path": str(backup_file),
                "promotion_ts": datetime.now(timezone.utc).isoformat(),
                "promotion_session_id": "sess-1",
            }
        )
    )

    result = rollback(
        proposal_id="prop-1",
        state_dir=str(state_dir),
        audit_log_path=str(audit_log),
    )

    assert result["status"] == "rolled_back"
    assert live_file.read_text() == "original"


def test_propose_tool_dispatches_to_real_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import reload_config
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.models.config._sub_models import MetaTuneConfig
    from trw_mcp.tools import meta_tune_ops

    server = make_test_server("meta_tune")
    propose = extract_tool_fn(server, "trw_meta_tune_propose")
    target = tmp_path / "CLAUDE.md"
    target.write_text("before")
    captured: dict[str, object] = {}

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
    )
    reload_config(cfg)

    class _FakeResult:
        def model_dump(self) -> dict[str, object]:
            return {"decision": "approve"}

    def _fake_promote_candidate(**kwargs: object) -> object:
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(meta_tune_ops, "promote_candidate", _fake_promote_candidate)
    try:
        result = propose(
            target_path=str(target),
            candidate_content="after",
            proposer_id="agent-1",
            sandbox_command=["python", "-c", "print('ok')"],
            reviewer_id="alice",
            approval_ts="2026-04-24T00:00:00+00:00",
        )
    finally:
        reload_config(None)

    assert captured["target_path"] == target
    assert captured["reviewer_id"] == "alice"
    assert captured["sandbox_command"] == ["python", "-c", "print('ok')"]
    assert result["decision"] == "approve"
