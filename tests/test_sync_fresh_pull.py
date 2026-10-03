"""SHARED-RECALL-LOCAL item 2: recall reads learnings fresh enough from the operator's other hosts.

When the last team pull is older than ``team_sync_fresh_after_seconds``,
session start and ``trw_recall`` first run a bounded, pull-only catch-up through
the sync cycle (paging by ``sync_seq``, because the backend caps one page at
``intel_state_team_learnings_limit``). The caller waits at most a small budget;
past it recall proceeds with what is local and says so.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class _FakeClient:
    """Stands in for BackendSyncClient: each pull-only cycle moves the real cursor one page."""

    def __init__(self, trw_dir: Path, *, pages_available: int, cycle_seconds: float = 0.0) -> None:
        from trw_mcp.sync.coordinator import SyncCoordinator

        self._trw_dir = trw_dir
        self._targets = [SimpleNamespace(label="primary")]
        self._coordinator = SyncCoordinator(trw_dir)
        self._pages_available = pages_available
        self._cycle_seconds = cycle_seconds
        self.calls: list[dict[str, bool]] = []

    async def _run_one_cycle(self, force: bool = False, *, push: bool = True, pull: bool = True) -> str:
        self.calls.append({"force": force, "push": push, "pull": pull})
        if self._cycle_seconds:
            await asyncio.sleep(self._cycle_seconds)
        seq = self._coordinator.get_last_pull_seq()
        if self._pages_available > 0:
            self._pages_available -= 1
            seq += 200
        self._coordinator.record_pull_success(pull_seq=seq)
        return "ok"


def _config(**overrides: Any) -> Any:
    from trw_mcp.models.config import TRWConfig

    return TRWConfig(**{"team_sync_enabled": True, **overrides})


def _last_pull(trw_dir: Path, ago: timedelta) -> None:
    from trw_mcp.sync.coordinator import SyncCoordinator

    coordinator = SyncCoordinator(trw_dir)
    coordinator.record_pull_success(pull_seq=0)
    state = coordinator._read_state()
    state["last_pull_at"] = (datetime.now(tz=timezone.utc) - ago).isoformat()
    coordinator._write_state(state)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


@pytest.fixture
def fake(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    from trw_mcp.sync import _fresh_pull

    client = _FakeClient(trw_dir, pages_available=3)
    monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "true")  # the suite defaults it off; these tests pull
    monkeypatch.setattr(_fresh_pull, "_client", lambda _config, _trw_dir: client)
    _fresh_pull.reset_inflight()
    return client


def test_a_stale_pull_pages_pull_only_until_the_cursor_stops(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    _last_pull(trw_dir, timedelta(minutes=30))

    answer = ensure_fresh(trw_dir, _config())

    assert answer == {"status": "ok", "pages": 4, "caught_up": True}
    assert fake.calls == [{"force": True, "push": False, "pull": True}] * 4
    assert fake._coordinator.get_last_pull_seq() == 600


def test_a_fresh_pull_asks_nothing(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    _last_pull(trw_dir, timedelta(seconds=30))

    assert ensure_fresh(trw_dir, _config()) is None
    assert fake.calls == []


def test_the_threshold_is_configurable(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    _last_pull(trw_dir, timedelta(seconds=30))

    assert ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=10)) is not None
    assert fake.calls


def test_a_host_that_never_pulled_pulls(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    answer = ensure_fresh(trw_dir, _config())

    assert answer is not None and answer["status"] == "ok"


@pytest.mark.parametrize("overrides", [{"team_sync_enabled": False}, {"team_sync_fresh_after_seconds": 0}])
def test_team_sync_off_or_threshold_zero_never_pulls(
    trw_dir: Path, fake: _FakeClient, overrides: dict[str, Any]
) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    assert ensure_fresh(trw_dir, _config(**overrides)) is None
    assert fake.calls == []


def test_the_catch_up_is_bounded_in_pages(trw_dir: Path, fake: _FakeClient) -> None:
    """A backlog deeper than the bound stops at it; the next call continues from the cursor."""
    from trw_mcp.sync._fresh_pull import MAX_PAGES, ensure_fresh

    fake._pages_available = MAX_PAGES + 5

    answer = ensure_fresh(trw_dir, _config())

    assert answer == {"status": "ok", "pages": MAX_PAGES, "caught_up": False}
    assert len(fake.calls) == MAX_PAGES


def test_a_capped_backlog_continues_on_the_next_call(trw_dir: Path, fake: _FakeClient) -> None:
    """A capped page batch is resumed immediately, but each call stays bounded."""
    from trw_mcp.sync._fresh_pull import MAX_PAGES, ensure_fresh

    fake._pages_available = MAX_PAGES + 1
    _last_pull(trw_dir, timedelta(minutes=30))

    first = ensure_fresh(trw_dir, _config())
    assert first == {"status": "ok", "pages": MAX_PAGES, "caught_up": False}
    assert len(fake.calls) == MAX_PAGES

    from trw_mcp.sync import _fresh_pull

    _fresh_pull.reset_inflight()
    second = ensure_fresh(trw_dir, _config())

    assert second == {"status": "ok", "pages": 2, "caught_up": True}
    assert len(fake.calls) == MAX_PAGES + 2


def test_a_failed_merge_is_not_a_catch_up_and_can_retry_later(trw_dir: Path, fake: _FakeClient) -> None:
    """A held cursor stays failed, is suppressed briefly, then is offered again."""
    from trw_mcp.sync._fresh_pull import ensure_fresh
    from trw_mcp.sync.coordinator import SyncCoordinator

    _last_pull(trw_dir, timedelta(minutes=30))
    original = fake._run_one_cycle

    async def fail_once(force: bool = False, *, push: bool = True, pull: bool = True) -> str:
        fake.calls.append({"force": force, "push": push, "pull": pull})
        fake._run_one_cycle = original  # type: ignore[method-assign]
        return "pull_failed"

    fake._run_one_cycle = fail_once  # type: ignore[method-assign]

    first = ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=60))
    assert first == {"status": "pull_failed", "pages": 0, "caught_up": False}

    assert ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=60)) is None
    assert len(fake.calls) == 1

    coordinator = SyncCoordinator(trw_dir)
    state = coordinator._read_state()
    state["last_pull_attempt_at"] = (datetime.now(tz=timezone.utc) - timedelta(minutes=2)).isoformat()
    coordinator._write_state(state)
    from trw_mcp.sync import _fresh_pull

    _fresh_pull.reset_inflight()
    later = ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=60))
    assert later == {"status": "ok", "pages": 4, "caught_up": True}
    assert len(fake.calls) == 5


def test_an_offline_client_setup_is_suppressed_for_the_threshold(
    trw_dir: Path, fake: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client construction fails open, but its timestamp survives another recall."""
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.sync.coordinator import SyncCoordinator

    _last_pull(trw_dir, timedelta(minutes=30))
    attempts = 0

    def offline(_config: object, _trw_dir: Path) -> _FakeClient:
        nonlocal attempts
        attempts += 1
        state = SyncCoordinator(trw_dir)._read_state()
        assert state.get("last_pull_attempt_at")
        raise OSError("offline")

    monkeypatch.setattr(_fresh_pull, "_client", offline)

    _fresh_pull.ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=60))
    _fresh_pull.reset_inflight()
    _fresh_pull.ensure_fresh(trw_dir, _config(team_sync_fresh_after_seconds=60))

    assert attempts == 1


@pytest.mark.parametrize("setup", ["state", "client"])
def test_setup_time_counts_toward_the_budget(
    trw_dir: Path, fake: _FakeClient, monkeypatch: pytest.MonkeyPatch, setup: str
) -> None:
    from trw_mcp.sync import _fresh_pull

    if setup == "state":

        def slow_state(_trw_dir: Path) -> tuple[None, bool]:
            time.sleep(0.06)
            return None, False

        monkeypatch.setattr(_fresh_pull, "_read_pull_state", slow_state)
    else:

        def slow_client(_config: object, _trw_dir: Path) -> _FakeClient:
            time.sleep(0.06)
            return fake

        monkeypatch.setattr(_fresh_pull, "_client", slow_client)

    answer = _fresh_pull.ensure_fresh(trw_dir, _config(), budget_seconds=0.02)

    assert answer is not None and answer["status"] == "timeout"
    assert fake.calls == []


def test_lock_wait_counts_toward_the_budget(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync import _fresh_pull

    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _fresh_pull._lock:
            acquired.set()
            release.wait(timeout=0.3)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert acquired.wait(timeout=1)
    started = time.monotonic()
    try:
        answer = _fresh_pull.ensure_fresh(trw_dir, _config(), budget_seconds=0.03)
    finally:
        release.set()
        holder.join(timeout=1)

    assert time.monotonic() - started < 0.2
    assert answer is not None and answer["status"] == "timeout"
    assert fake.calls == []


def test_a_slow_pull_never_blocks_past_the_budget(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    fake._cycle_seconds = 0.5
    started = time.monotonic()

    answer = ensure_fresh(trw_dir, _config(), budget_seconds=0.2)

    assert time.monotonic() - started < 0.45
    assert answer is not None and answer["status"] == "timeout"
    assert "local" in str(answer["detail"])


def test_a_call_that_finds_a_pull_running_returns_at_once_and_does_not_restart_it(
    trw_dir: Path, fake: _FakeClient
) -> None:
    """Only the call that STARTS a catch-up waits its budget; every later call during it costs nothing."""
    from trw_mcp.sync._fresh_pull import ensure_fresh

    fake._cycle_seconds = 0.6
    fake._pages_available = 0
    assert ensure_fresh(trw_dir, _config(), budget_seconds=0.05)["status"] == "timeout"  # type: ignore[index]

    started = time.monotonic()
    answer = ensure_fresh(trw_dir, _config(), budget_seconds=2.0)

    assert time.monotonic() - started < 0.2
    assert answer == {"status": "in_flight"}
    assert len(fake.calls) == 1


def test_a_failed_pull_is_reported_not_raised(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    async def failing(force: bool = False, *, push: bool = True, pull: bool = True) -> str:
        fake.calls.append({"force": force, "push": push, "pull": pull})
        return "pull_failed"

    fake._run_one_cycle = failing  # type: ignore[method-assign]

    assert ensure_fresh(trw_dir, _config()) == {"status": "pull_failed", "pages": 0, "caught_up": False}


def test_no_sync_target_asks_nothing(trw_dir: Path, fake: _FakeClient) -> None:
    from trw_mcp.sync._fresh_pull import ensure_fresh

    fake._targets = []

    assert ensure_fresh(trw_dir, _config()) is None
    assert fake.calls == []


def test_recall_says_so_when_the_pull_did_not_finish_in_time(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.tools._recall_impl import execute_recall

    timeout = {"status": "timeout", "detail": "recall used local rows"}
    asked: list[Path] = []
    monkeypatch.setattr(_fresh_pull, "ensure_fresh", lambda d, _c, **_k: asked.append(d) or timeout)

    result = execute_recall(
        "wheel cache", trw_dir, _config(), _adapter_recall=lambda *_a, **_k: [], _rank_by_utility=lambda m, *_a, **_k: m
    )

    assert asked == [trw_dir]
    assert result["fresh_pull"] == timeout  # type: ignore[typeddict-item]


def test_recall_adds_nothing_when_the_pull_finished(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.tools._recall_impl import execute_recall

    monkeypatch.setattr(_fresh_pull, "ensure_fresh", lambda *_a, **_k: {"status": "ok", "pages": 1, "caught_up": True})

    result = execute_recall(
        "wheel cache", trw_dir, _config(), _adapter_recall=lambda *_a, **_k: [], _rank_by_utility=lambda m, *_a, **_k: m
    )

    assert "fresh_pull" not in result


def test_session_start_reports_the_fresh_pull(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.tools import _ceremony_step_table as table
    from trw_mcp.tools import ceremony

    monkeypatch.setattr(ceremony, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(_fresh_pull, "ensure_fresh", lambda *_a, **_k: {"status": "ok", "pages": 2, "caught_up": True})
    sctx = SimpleNamespace(config=_config(), results={})

    table._ss_fresh_pull(sctx)  # type: ignore[arg-type]

    assert sctx.results["fresh_pull"] == {"status": "ok", "pages": 2, "caught_up": True}
    names = [step.key for step in table.SESSION_START_STEPS]
    assert names.index("fresh_pull") == names.index("recall") - 1


def test_another_process_failed_pull_does_not_hold_back_the_recall_catch_up(trw_dir: Path, fake: _FakeClient) -> None:
    """O6 on dev27: one aborted cycle from a short-lived process (an installer smoke start) silenced a brand-new host's
    first catch-up for 5 minutes. A failure recorded by another process is not this recall's attempt."""
    from trw_mcp.sync._fresh_pull import ensure_fresh
    from trw_mcp.sync.coordinator import SyncCoordinator

    SyncCoordinator(trw_dir).record_sync_failure("pull failed")  # never pulled, and some other process just failed

    answer = ensure_fresh(trw_dir, _config())

    assert answer is not None and answer["status"] == "ok"
    assert fake.calls  # the catch-up ran


def test_a_recent_attempt_of_its_own_is_not_repeated_by_every_recall(trw_dir: Path, fake: _FakeClient) -> None:
    """During an outage a recall pays its budget once per threshold: its own attempt stamp holds back the next."""
    from trw_mcp.sync._fresh_pull import ensure_fresh, reset_inflight

    fake._pages_available = 0

    async def fail(force: bool = False, *, push: bool = True, pull: bool = True) -> str:
        fake.calls.append({"force": force, "push": push, "pull": pull})
        return "pull_failed"

    fake._run_one_cycle = fail  # type: ignore[method-assign]

    first = ensure_fresh(trw_dir, _config())
    reset_inflight()
    second = ensure_fresh(trw_dir, _config())

    assert first == {"status": "pull_failed", "pages": 0, "caught_up": False}
    assert second is None
    assert len(fake.calls) == 1


def test_a_recall_with_platform_contact_off_does_not_touch_the_sync_state(
    trw_dir: Path, fake: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O6's control leg: contact off cannot pull, so starting a catch-up only records a false failure and an attempt that
    silences the next (contact-on) recall for the whole threshold."""
    from trw_mcp.models.config import _reset_config
    from trw_mcp.sync._fresh_pull import ensure_fresh
    from trw_mcp.sync.coordinator import SyncCoordinator

    monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "false")
    _reset_config()

    assert ensure_fresh(trw_dir, _config()) is None
    assert fake.calls == []  # no catch-up started
    state = SyncCoordinator(trw_dir)._read_state()
    assert not state.get("last_pull_attempt_at") and not state.get("last_error")  # and nothing recorded

    monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "true")
    _reset_config()  # a separate process in real use; here the cached config would still veto
    answer = ensure_fresh(trw_dir, _config())  # the next recall, contact on, is not silenced

    assert answer is not None and answer["status"] == "ok"
    assert fake.calls
