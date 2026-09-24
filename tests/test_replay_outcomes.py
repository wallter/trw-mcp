"""PRD-CORE-144 FR07 rollout telemetry and FR08 historical replay."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastmcp import FastMCP

from trw_mcp.tools.replay import register_replay_tools, replay_pending_outcomes

_RUN_YAML = """session_metrics:
  status: success
  rework_rate:
    rework_rate: 0.25
    total_files: 2
  learning_exposure:
    ids: [L-1]
"""


def _mk_run(runs_root: Path, task: str, run_id: str) -> Path:
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(_RUN_YAML, encoding="utf-8")
    return run_dir


async def _push_ok(payloads: list[dict[str, object]]) -> bool:
    return bool(payloads)


def test_replay_without_env_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TRW_ALLOW_REPLAY", raising=False)
    _mk_run(tmp_path / "runs", "t", "r")
    result = asyncio.run(replay_pending_outcomes(tmp_path, push_outcomes=_push_ok))
    assert result["gated"] is True
    assert result["replayed"] == 0


def test_replay_pushes_unsynced_once_then_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
    runs_root = tmp_path / "runs"
    r1 = _mk_run(runs_root, "t", "r1")
    r2 = _mk_run(runs_root, "t", "r2")

    first = asyncio.run(replay_pending_outcomes(tmp_path, push_outcomes=_push_ok))
    second = asyncio.run(replay_pending_outcomes(tmp_path, push_outcomes=_push_ok))

    assert first["replayed"] == 2
    assert second["replayed"] == 0
    assert (r1 / "meta" / "synced.json").exists()
    assert (r2 / "meta" / "synced.json").exists()


def test_replay_respects_old_run_cutoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
    runs_root = tmp_path / "runs"
    old = _mk_run(runs_root, "t", "old")
    new = _mk_run(runs_root, "t", "new")
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=5)).timestamp()
    os.utime(old, (old_ts, old_ts))

    cutoff = (now - timedelta(days=1)).isoformat()
    result = asyncio.run(replay_pending_outcomes(tmp_path, since=cutoff, push_outcomes=_push_ok))

    assert result["replayed"] == 1
    assert (old / "meta" / "synced.json").exists()
    assert not (new / "meta" / "synced.json").exists()


def test_replay_push_failure_preserves_pending_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
    run_dir = _mk_run(tmp_path / "runs", "t", "r")

    async def fail(_payloads: list[dict[str, object]]) -> bool:
        return False

    result = asyncio.run(replay_pending_outcomes(tmp_path, push_outcomes=fail))
    assert result["failed"] == 1
    assert not (run_dir / "meta" / "synced.json").exists()


def test_replay_tool_is_registered() -> None:
    server = FastMCP("replay-test")
    register_replay_tools(server)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "trw_replay_outcomes" in names
