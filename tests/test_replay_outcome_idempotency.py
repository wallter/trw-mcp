"""Crash/retry and concurrent-producer outcome replay contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trw_mcp.sync.outcomes import load_pending_outcomes
from trw_mcp.tools.replay import replay_pending_outcomes

_RUN_YAML = """session_metrics:
  status: success
  build_passed: true
  learning_exposure:
    ids: [L-1]
"""


def _make_run(trw_dir: Path) -> Path:
    run_dir = trw_dir / "runs" / "task" / "run-1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(_RUN_YAML, encoding="utf-8")
    return run_dir


def test_pending_payload_uses_content_sync_hash_as_idempotency_key(tmp_path: Path) -> None:
    _make_run(tmp_path)

    item = load_pending_outcomes(tmp_path)[0]

    assert item.payload["idempotency_key"] == item.sync_hash
    assert len(item.sync_hash) == 64


async def test_crash_after_remote_acceptance_retries_same_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
    run_dir = _make_run(tmp_path)
    accepted: set[str] = set()
    attempts: list[str] = []

    async def accept_then_crash(payloads: list[dict[str, object]]) -> bool:
        key = str(payloads[0]["idempotency_key"])
        attempts.append(key)
        accepted.add(key)
        if len(attempts) == 1:
            raise RuntimeError("transport lost after durable acceptance")
        return True

    with pytest.raises(RuntimeError, match="transport lost"):
        await replay_pending_outcomes(tmp_path, push_outcomes=accept_then_crash)
    assert not (run_dir / "meta" / "synced.json").exists()

    result = await replay_pending_outcomes(tmp_path, push_outcomes=accept_then_crash)

    assert result["replayed"] == 1
    assert attempts[0] == attempts[1]
    assert len(accepted) == 1
    assert (run_dir / "meta" / "synced.json").exists()


async def test_two_replay_producers_converge_on_one_backend_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
    _make_run(tmp_path)
    both_started = asyncio.Event()
    attempts: list[str] = []
    accepted: set[str] = set()

    async def deduplicating_backend(payloads: list[dict[str, object]]) -> bool:
        key = str(payloads[0]["idempotency_key"])
        attempts.append(key)
        if len(attempts) == 2:
            both_started.set()
        await both_started.wait()
        accepted.add(key)
        return True

    first, second = await asyncio.gather(
        replay_pending_outcomes(tmp_path, push_outcomes=deduplicating_backend),
        replay_pending_outcomes(tmp_path, push_outcomes=deduplicating_backend),
    )

    assert first["replayed"] == 1
    assert second["replayed"] == 1
    assert attempts[0] == attempts[1]
    assert len(accepted) == 1
