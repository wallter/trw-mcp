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
    from trw_mcp.models.config import reload_config
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.models.config._sub_models import MetaTuneConfig

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

    # The kill switch must be explicitly enabled via config — the tool no longer
    # force-enables a disabled subsystem just because an audit_log_path is given.
    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=True)))
    try:
        result = rollback(
            proposal_id="prop-1",
            state_dir=str(state_dir),
            audit_log_path=str(audit_log),
        )
    finally:
        reload_config(None)

    assert result["status"] == "rolled_back"
    assert live_file.read_text() == "original"


def test_propose_tool_dispatches_to_real_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_dispatch_replay_inherits_full_env_not_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint-97 adaptive-surface review F1: the SAFE-001 candidate-replay
    sandbox call MUST pass the full inherited environment (env=os.environ.copy())
    so PYTHONPATH / VIRTUAL_ENV / TRW_* reach the replay subprocess. The sandbox
    sanitizes the env by default (CORE-144 §7.6); without the explicit env the
    replay fails for ENV reasons and proposals are scored as refuted.
    """
    import os

    from trw_mcp.meta_tune import promote
    from trw_mcp.meta_tune.sandbox import SandboxResult
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.models.config._sub_models import MetaTuneConfig

    # Seed marker env vars the sanitized minimal env (PATH/HOME/LANG/...) drops.
    monkeypatch.setenv("PYTHONPATH", "/seed/pythonpath")
    monkeypatch.setenv("VIRTUAL_ENV", "/seed/venv")
    monkeypatch.setenv("TRW_SENTINEL", "trw-replay-marker")

    captured_env: dict[str, dict[str, str] | None] = {}

    def _fake_run_sandboxed(cmd: list[str], **kwargs: object) -> SandboxResult:
        env = kwargs.get("env")
        captured_env["env"] = env if env is None else dict(env)  # type: ignore[arg-type]
        return SandboxResult(
            exit_code=0,
            stdout=json.dumps({"declared_metric_delta": 0.25}),
            stderr="",
            wall_ms=1.0,
            rss_peak_mb=1.0,
            network_attempted=False,
            writes_outside_tmp=[],
            timed_out=False,
        )

    monkeypatch.setattr(promote, "run_sandboxed", _fake_run_sandboxed)

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(tmp_path / "audit" / "audit.jsonl"),
            sandbox_timeout_seconds=3.0,
        )
    )
    target = tmp_path / "CLAUDE.md"
    target.write_text("before")

    promote.promote_candidate(
        target_path=target,
        candidate_content="after",
        proposer_id="agent-1",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('ok')"],
        declared_metric_delta=0.25,
        state_dir=tmp_path / "state",
        _config=cfg,
    )

    env = captured_env["env"]
    # The dispatch path must pass an explicit env (NOT None → not minimal-sanitized).
    assert env is not None, "candidate replay must pass an explicit env, not the sanitized default"
    # Full inheritance: marker vars stripped by _SAFE_ENV_KEYS sanitization survive.
    assert env.get("PYTHONPATH") == "/seed/pythonpath"
    assert env.get("VIRTUAL_ENV") == "/seed/venv"
    assert env.get("TRW_SENTINEL") == "trw-replay-marker"
    # Sanity: it really is the full parent env, mirroring os.environ.
    assert env.get("PYTHONPATH") == os.environ.get("PYTHONPATH")


def test_rollback_tool_stays_disabled_when_kill_switch_off(tmp_path: Path) -> None:
    """SAFE-001 FR-7/FR-13: the rollback tool must NOT override the global kill
    switch. With meta_tune.enabled=False the tool must surface status=disabled
    and never mutate the target file, even when an audit_log_path is supplied."""
    from trw_mcp.models.config import reload_config
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.models.config._sub_models import MetaTuneConfig

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

    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=False)))
    try:
        # audit_log_path supplied — previously this branch force-enabled the
        # subsystem; it must no longer do so.
        result = rollback(
            proposal_id="prop-1",
            state_dir=str(state_dir),
            audit_log_path=str(audit_log),
        )
    finally:
        reload_config(None)

    assert result["status"] == "disabled"
    assert result["reason"] == "kill_switch_off"
    # The disabled subsystem must not have touched the live file.
    assert live_file.read_text() == "mutated"
