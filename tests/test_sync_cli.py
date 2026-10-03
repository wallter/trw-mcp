"""INC-145: ``trw-mcp sync push | pull | status`` on CLI-only and headless hosts.

A learning used to leave a host only from the long-lived MCP server loop,
about 300 s after start, so a CLI-only host never pushed. These verbs run the
SAME cycle the server loop runs (``run_one_cycle``), only on demand. The
coordinator, its ``sync-state.json`` and the store are real or faked locally;
the backend is a fake pusher and puller, never the network.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._test_sync_client_support import _make_config
from trw_mcp.sync._team_merge_result import TeamMergeResult
from trw_mcp.sync.coordinator import SyncCoordinator


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore) -> dict[str, Any]:
    """A configured host with a fake backend: returns the pusher and puller mocks and the store."""
    from trw_mcp.sync.pull import PullResult, SyncPuller
    from trw_mcp.sync.push import PushResult, SyncPusher

    config = {"value": _make_config(platform_telemetry_enabled=False)}
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config["value"])
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: tmp_path)
    monkeypatch.setattr("trw_mcp.sync.client.resolve_sync_client_id", lambda: "cli-client")
    push = AsyncMock(side_effect=lambda entries: PushResult(pushed=len(entries)))
    monkeypatch.setattr(SyncPusher, "push_learnings", push)
    pull = AsyncMock(
        return_value=PullResult(
            team_learnings=[{"source_learning_id": "R-1", "sync_seq": 9}], status_code=200, state={}, etag=None
        )
    )
    monkeypatch.setattr(SyncPuller, "pull_intel_state", pull)
    monkeypatch.setattr(
        SyncPuller, "merge_team_learnings", lambda self, items: TeamMergeResult(attempted=1, inserted=1)
    )
    return {"push": push, "pull": pull, "store": fake_memory_store, "config": config, "trw_dir": tmp_path}


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, dict[str, Any] | None, str]:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands_sync import run_sync

    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = 0
    try:
        run_sync(_build_arg_parser().parse_args(["sync", *argv]))
    except SystemExit as exited:
        code = int(exited.code or 0)
    lines = [line for line in out.getvalue().splitlines() if line.startswith("{")]
    return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


def test_the_sync_family_lists_push_pull_and_status() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    for verb in ("push", "pull", "status"):
        assert parser.parse_args(["sync", verb]).sync_command == verb


def test_sync_push_pushes_dirty_learnings_through_the_cycle_and_does_not_pull(
    host: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    host["store"].put("a learning", FAKE_NAMESPACE, {"entry_id": "L-1"})

    code, report, _ = _cli(monkeypatch, "push")

    assert code == 0
    host["push"].assert_awaited_once()
    host["pull"].assert_not_awaited()
    assert report is not None
    assert report["pending"] == 0
    assert report["last_push_at"]
    assert report["last_pull_at"] is None
    assert report["last_content_push_at"]  # the cycle sent entries, so it is egress evidence


def test_sync_push_holds_a_rejected_learning_and_says_so(host: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.sync.push import PushResult

    host["store"].put("refused", FAKE_NAMESPACE, {"entry_id": "L-bad"})
    host["store"].put("fine", FAKE_NAMESPACE, {"entry_id": "L-ok"})
    host["push"].side_effect = lambda entries: PushResult(pushed=1, rejected={"L-bad": "HTTP 422: type: bad"})

    code, report, err = _cli(monkeypatch, "push")

    assert code == 0
    assert report is not None
    assert report["rejected"] == 1
    assert report["rejected_entries"] == [{"id": "L-bad", "reason": "HTTP 422: type: bad"}]
    assert report["pending"] == 0  # the held entry is not counted as pending
    assert "1 learning(s) rejected" in err
    assert "--retry-rejected" in err


def test_sync_push_retry_rejected_re_offers_held_learnings(
    host: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    host["store"].put("refused", FAKE_NAMESPACE, {"entry_id": "L-bad"})
    seq = host["store"].rows[(FAKE_NAMESPACE, "L-bad")].sync_seq
    SyncCoordinator(trw_dir=host["trw_dir"]).update_rejected(add={"L-bad": (seq, "HTTP 422: old")})

    code, _, _ = _cli(monkeypatch, "push")
    assert code == 0
    host["push"].assert_not_awaited()  # held: nothing to send

    code, report, _ = _cli(monkeypatch, "push", "--retry-rejected")

    assert code == 0
    sent = host["push"].await_args.args[0]
    assert [entry.id for entry in sent] == ["L-bad"]
    assert report is not None
    assert report["rejected"] == 0


def test_sync_push_exits_1_with_the_servers_reason_when_the_push_fails(
    host: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.sync.push import PushResult

    host["store"].put("a learning", FAKE_NAMESPACE, {"entry_id": "L-1"})
    host["push"].side_effect = lambda entries: PushResult(failed=1, last_error="HTTP 503: maintenance window")

    code, report, err = _cli(monkeypatch, "push")

    assert code == 1
    assert report is not None
    assert report["consecutive_failures"] == 1
    assert "HTTP 503: maintenance window" in err


def test_sync_push_refuses_when_learning_sharing_is_off(host: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    host["config"]["value"] = _make_config(learning_sharing_enabled=False)

    code, _, err = _cli(monkeypatch, "push")

    assert code == 2
    assert "learning_sharing_enabled is false" in err
    host["push"].assert_not_awaited()


def test_sync_push_names_the_pid_holding_the_lock(host: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    with SyncCoordinator(trw_dir=host["trw_dir"]).acquire_sync_lock() as acquired:
        assert acquired
        code, _, err = _cli(monkeypatch, "push")

    assert code == 2
    assert f"pid {os.getpid()}" in err


def test_sync_pull_merges_without_pushing_or_claiming_a_push(
    host: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    host["store"].put("local", FAKE_NAMESPACE, {"entry_id": "L-1"})

    code, report, _ = _cli(monkeypatch, "pull")

    assert code == 0
    host["pull"].assert_awaited_once()
    host["push"].assert_not_awaited()
    assert report is not None
    assert report["last_pull_at"]
    assert report["last_push_at"] is None
    assert report["pending"] == 1


def test_sync_pull_refuses_when_team_sync_is_off(host: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    host["config"]["value"] = _make_config(team_sync_enabled=False)

    code, _, err = _cli(monkeypatch, "pull")

    assert code == 2
    assert "team_sync_enabled is false" in err


def test_sync_status_reports_without_any_network_call(host: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    host["store"].put("one", FAKE_NAMESPACE, {"entry_id": "L-1"})
    host["store"].put("two", FAKE_NAMESPACE, {"entry_id": "L-2"})
    SyncCoordinator(trw_dir=host["trw_dir"]).update_rejected(add={"L-9": (4, "HTTP 422: type: bad")})

    code, report, _ = _cli(monkeypatch, "status")

    assert code == 0
    host["push"].assert_not_awaited()
    host["pull"].assert_not_awaited()
    assert report is not None
    assert report["pending"] == 2
    assert report["rejected"] == 1
    assert report["targets"] == ["example.com"]
    assert (report["last_push_at"], report["last_pull_at"]) == (None, None)
    assert (report["learning_sharing_enabled"], report["team_sync_enabled"]) == (True, True)
