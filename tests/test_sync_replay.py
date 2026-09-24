"""``trw-mcp sync pull --full``: a resumable replay of every team learning (PRD-CORE-280 FR02).

The coordinator and its ``sync-state.json`` are real; only the platform is a
fake that pages team learnings by ``sync_seq``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._test_sync_client_support import _make_config
from trw_mcp.sync._team_merge_result import TeamMergeResult

PAGE = 3


def _learning(source_id: str, seq: int, *, source: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_learning_id": source_id,
        "summary": f"tip {source_id}",
        "detail": "detail",
        "vector_clock": {"peer": 1},
        "sync_seq": seq,
    }
    if source is not None:
        row["metadata"] = {"source": source}
    return row


class _Platform:
    """Pages learnings 1..count after ``since_seq``; the merge can crash or fail on chosen calls."""

    def __init__(
        self, count: int, *, company: int = 0, crash_on: int | None = None, fail_on: set[int] | None = None
    ) -> None:
        self.items = [_learning(f"L-{seq}", seq) for seq in range(1, count + 1)]
        self.company = [_learning(f"C-{seq}", seq, source="company_sync") for seq in range(1, company + 1)]
        self.merged: list[str] = []
        self.merge_calls = 0
        self.crash_on, self.fail_on = crash_on, fail_on or set()

    async def pull_intel_state(self, *, since_seq: int, since_company_seq: int, **_: object) -> Any:
        from trw_mcp.sync.pull import PullResult

        page = [item for item in self.items if int(item["sync_seq"]) > since_seq][:PAGE]
        company = [item for item in self.company if int(item["sync_seq"]) > since_company_seq][:PAGE]
        next_company = max([since_company_seq, *(int(item["sync_seq"]) for item in company)])
        return PullResult(team_learnings=company + page, status_code=200, next_company_seq=next_company)

    def merge_team_learnings(self, items: list[dict[str, Any]]) -> TeamMergeResult:
        call, self.merge_calls = self.merge_calls, self.merge_calls + 1
        if call == self.crash_on:
            raise RuntimeError("process killed mid-page")
        if call in self.fail_on:
            return TeamMergeResult(attempted=len(items), failed=len(items))
        self.merged.extend(str(item["source_learning_id"]) for item in items)
        return TeamMergeResult(attempted=len(items), inserted=len(items))


def _client(trw_dir: Path, platform: _Platform) -> Any:
    from trw_mcp.sync.client import BackendSyncClient

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="replay-client"):
        client = BackendSyncClient(_make_config(), trw_dir)
    client._puller = platform
    return client


async def _replay(client: Any, trw_dir: Path, **options: Any) -> Any:
    from trw_mcp.sync._replay import run_full_pull

    options.setdefault("resume", False)
    options.setdefault("max_pages", 100)
    return await run_full_pull(client, receipt_path=trw_dir / "sync-replay.jsonl", **options)


async def test_a_full_pull_merges_every_page_and_clears_its_block(tmp_path: Path) -> None:
    platform = _Platform(7)
    client = _client(tmp_path, platform)

    report = await _replay(client, tmp_path)

    assert report == {"status": "completed", "pages": 3, "pulled": 7, "merged": 7, "next_seq": 7}
    assert platform.merged == [f"L-{seq}" for seq in range(1, 8)]
    assert client._coordinator.replay_state() is None
    assert client._coordinator.get_last_pull_seq() == 7
    receipts = [json.loads(line) for line in (tmp_path / "sync-replay.jsonl").read_text().splitlines()]
    assert [(row["since_seq"], row["outcome"]) for row in receipts] == [
        (0, "page"),
        (3, "page"),
        (6, "page"),
        (7, "end"),
    ]


@pytest.mark.parametrize("crash_on", [0, 1, 2])
async def test_a_run_killed_on_any_page_resumes_without_losing_a_learning(tmp_path: Path, crash_on: int) -> None:
    platform = _Platform(7, crash_on=crash_on)
    client = _client(tmp_path, platform)

    with pytest.raises(RuntimeError):
        await _replay(client, tmp_path)
    refused = await _replay(client, tmp_path)
    resumed = await _replay(client, tmp_path, resume=True)

    assert refused["status"] == "unfinished"
    assert refused["next_seq"] == crash_on * PAGE
    assert resumed["status"] == "completed"
    assert platform.merged == [f"L-{seq}" for seq in range(1, 8)]
    assert client._coordinator.replay_state() is None


async def test_a_page_the_merge_cannot_judge_holds_the_block_after_three_tries(tmp_path: Path) -> None:
    from trw_mcp.sync._replay import PAGE_ATTEMPTS

    platform = _Platform(7, fail_on={1, 2, 3})
    client = _client(tmp_path, platform)

    held = await _replay(client, tmp_path)
    resumed = await _replay(client, tmp_path, resume=True)

    assert (held["status"], held["next_seq"], held["pages"]) == ("held", 3, 1)
    assert platform.merge_calls >= 1 + PAGE_ATTEMPTS
    assert resumed["status"] == "completed"
    assert platform.merged == [f"L-{seq}" for seq in range(1, 8)]


async def test_the_page_bound_pauses_and_resume_continues(tmp_path: Path) -> None:
    platform = _Platform(7)
    client = _client(tmp_path, platform)

    paused = await _replay(client, tmp_path, max_pages=1)
    resumed = await _replay(client, tmp_path, resume=True)

    assert (paused["status"], paused["next_seq"]) == ("paused", 3)
    assert resumed["status"] == "completed"
    assert platform.merged == [f"L-{seq}" for seq in range(1, 8)]


async def test_a_running_cycle_holds_the_lock_and_the_replay_does_not_start(tmp_path: Path) -> None:
    platform = _Platform(7)
    client = _client(tmp_path, platform)

    with client._coordinator.acquire_sync_lock() as acquired:
        assert acquired
        report = await _replay(client, tmp_path)

    assert report["status"] == "locked"
    assert platform.merge_calls == 0


async def test_the_replay_never_rewinds_the_periodic_cursor(tmp_path: Path) -> None:
    platform = _Platform(4)
    client = _client(tmp_path, platform)
    client._coordinator.record_sync_success(pushed=0, pulled=0, pull_seq=50, pull_completed=True)

    await _replay(client, tmp_path)

    assert client._coordinator.get_last_pull_seq() == 50
    assert platform.merged == ["L-1", "L-2", "L-3", "L-4"]


def test_the_cli_refuses_when_team_sync_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands_sync import run_sync

    args = _build_arg_parser().parse_args(["sync", "pull", "--full", "--resume"])
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _make_config(team_sync_enabled=False))

    with pytest.raises(SystemExit) as exited:
        run_sync(args)

    assert exited.value.code == 2
    assert (args.resume, args.max_pages, args.wait_seconds) == (True, 1000, 0.0)


async def test_a_page_of_company_rows_alone_does_not_end_the_replay(tmp_path: Path) -> None:
    """Company rows move only the company cursor; the org pages after them still arrive."""
    from trw_mcp.sync.pull import PullResult

    platform = _Platform(4)
    org_pages = platform.pull_intel_state

    async def company_first(*, since_seq: int, since_company_seq: int, **rest: object) -> Any:
        if since_company_seq == 0:
            company = [{"source_learning_id": "C-1", "sync_seq": 9, "metadata": {"source": "company_sync"}}]
            return PullResult(team_learnings=company, status_code=200, next_company_seq=9)
        return await org_pages(since_seq=since_seq, since_company_seq=since_company_seq, **rest)

    platform.pull_intel_state = company_first  # type: ignore[method-assign]
    client = _client(tmp_path, platform)

    report = await _replay(client, tmp_path)

    assert report["status"] == "completed"
    assert platform.merged == ["C-1", "L-1", "L-2", "L-3", "L-4"]
    assert client._coordinator.get_last_company_pull_seq() == 9


async def test_a_fresh_replay_starts_the_company_cursor_at_zero_too(tmp_path: Path) -> None:
    """A restored store lost its company rows as well; the high-water mark must not skip them."""
    platform = _Platform(2, company=4)
    client = _client(tmp_path, platform)
    client._coordinator.record_company_pull_seq(50)

    report = await _replay(client, tmp_path)

    assert report["status"] == "completed"
    assert sorted(platform.merged) == ["C-1", "C-2", "C-3", "C-4", "L-1", "L-2"]
    assert client._coordinator.get_last_company_pull_seq() == 50


async def test_replayed_rows_land_clean_and_a_repeated_replay_changes_nothing(
    daemon_checkout: DaemonCheckout,
) -> None:
    """Through the checkout's real store: a killed run resumes, nothing is re-pushed, and replaying again merges nothing."""
    from unittest.mock import AsyncMock, MagicMock

    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.sync._client_cycle import run_one_cycle
    from trw_mcp.sync.pull import SyncPuller

    trw_dir = daemon_checkout.trw_dir
    platform = _Platform(7)
    client = _client(trw_dir, platform)
    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="c", trw_dir=trw_dir)
    merges = 0

    def merge_crashing_once(items: list[dict[str, Any]]) -> TeamMergeResult:
        nonlocal merges
        merges += 1
        if merges == 2:
            raise RuntimeError("process killed mid-page")
        return puller.merge_team_learnings(items)

    client._puller = MagicMock(pull_intel_state=platform.pull_intel_state, merge_team_learnings=merge_crashing_once)
    with pytest.raises(RuntimeError):
        await _replay(client, trw_dir)
    resumed = await _replay(client, trw_dir, resume=True)
    again = await _replay(client, trw_dir)
    client._pusher = MagicMock(push_learnings=AsyncMock())
    await run_one_cycle(client, force=True)

    store, namespace = selected_store(trw_dir)
    stored = sorted(entry.id for entry in store.list_entries(namespace, limit=50))
    assert resumed["status"] == again["status"] == "completed"
    assert stored == sorted(f"team-sync-L-{seq}" for seq in range(1, 8))
    assert again["merged"] == 0
    assert store.page_dirty(namespace, 50) == []
    client._pusher.push_learnings.assert_not_called()


def _cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, str]:
    import io
    import sys

    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands_sync import run_sync

    monkeypatch.setattr("trw_mcp.models.config.get_config", _make_config)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: tmp_path)
    monkeypatch.setattr("trw_mcp.sync.client.resolve_sync_client_id", lambda: "replay-client")
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(SystemExit) as exited:
        run_sync(_build_arg_parser().parse_args(["sync", "pull", "--full", *argv]))
    return int(exited.value.code or 0), err.getvalue()


def test_the_cli_names_the_pid_holding_the_sync_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from trw_mcp.sync.coordinator import SyncCoordinator

    with SyncCoordinator(trw_dir=tmp_path, sync_interval=300).acquire_sync_lock() as acquired:
        assert acquired
        code, err = _cli(tmp_path, monkeypatch)

    assert code == 2
    assert f"pid {os.getpid()}" in err


@pytest.mark.parametrize("argv", [("--max-pages", "0"), ("--wait-seconds", "-1"), ("--wait-seconds", "nan")])
def test_the_cli_refuses_a_bad_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, str]) -> None:
    code, err = _cli(tmp_path, monkeypatch, *argv)

    assert code == 2
    assert "--max-pages must be at least 1" in err
