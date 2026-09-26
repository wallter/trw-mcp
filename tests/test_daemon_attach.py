"""PRD-CORE-298 FR01 -- a migrated checkout reaches memory only through the daemon.

With ``project_namespace`` pinned, ``trw_learn`` and ``trw_learn_update`` run
with the SQLite connection factory patched to raise: every row lands in the
daemon's store under the pinned namespace, and the checkout never opens a
``memory.db``. An unreachable daemon or a missing grant fails closed naming
``trw-mcp doctor`` and never falls back to the file.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonClient, DaemonPaths, mint_grant, write_checkout_grant

from tests._memory_daemon import running_daemon
from trw_mcp.models.config import reload_config
from trw_mcp.state._memory_update import update_learning
from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.state._tier_routing import USER_NAMESPACE
from trw_mcp.state.memory_adapter import store_learning

_PINNED = "project:attach-11111111"


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path / "repo"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    trw_dir = tmp_path / "repo" / ".trw"
    trw_dir.mkdir(parents=True)
    (trw_dir / "config.yaml").write_text(f"project_namespace: {_PINNED}\n", encoding="utf-8")
    reload_config()
    yield trw_dir
    reload_config()


def _refuse_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("a migrated checkout opened a SQLite connection")

    monkeypatch.setattr(sqlite3, "connect", _refuse)
    # The backend's own factory, whichever DB-API driver (sqlite3 or pysqlite3) it resolved.
    monkeypatch.setattr("trw_memory.storage._connection.connect", _refuse)


def test_learn_and_learn_update_go_through_the_daemon(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running_daemon(tmp_path / "userhome") as paths:
        token = mint_grant(paths, [_PINNED, USER_NAMESPACE])
        write_checkout_grant(checkout, token)
        _refuse_sqlite(monkeypatch)

        stored = store_learning(checkout, "L-att1", "Attach summary", "attach detail", impact=0.4)
        updated = update_learning(checkout, "L-att1", impact=0.9, detail="patched")

        monkeypatch.undo()
        row = asyncio.run(DaemonClient(token, paths=paths).get("L-att1", _PINNED))

    assert stored["status"] == "recorded"
    assert updated["status"] == "updated"
    assert (row["entry"]["namespace"], row["entry"]["importance"], row["entry"]["detail"]) == (_PINNED, 0.9, "patched")
    assert not list(checkout.rglob("memory.db"))


def test_an_unreachable_daemon_fails_closed_naming_doctor(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_checkout_grant(checkout, mint_grant(DaemonPaths.resolve(), [_PINNED, USER_NAMESPACE]))
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", lambda _paths: None)
    monkeypatch.setenv("MEMORY_DAEMON_STARTUP_TIMEOUT_SECONDS", "0.2")
    _refuse_sqlite(monkeypatch)

    with pytest.raises(StoreUnavailableError, match="trw-mcp doctor"):
        store_learning(checkout, "L-att2", "Never lands", "")

    assert not list(checkout.rglob("memory.db"))


def test_a_checkout_without_a_grant_fails_closed_naming_the_token_verb(checkout: Path) -> None:
    with pytest.raises(StoreUnavailableError, match="trw-mcp memory token"):
        store_learning(checkout, "L-att3", "Never lands", "")


def test_sync_pushes_and_pulls_only_the_pinned_namespace_through_the_daemon(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.sync._client_runtime import get_dirty_entries, mark_synced
    from trw_mcp.sync.pull import SyncPuller

    with running_daemon(tmp_path / "userhome") as paths:
        token = mint_grant(paths, [_PINNED, USER_NAMESPACE])
        write_checkout_grant(checkout, token)
        client = DaemonClient(token, paths=paths)
        asyncio.run(client.store("Portable, never pushed", USER_NAMESPACE))
        _refuse_sqlite(monkeypatch)

        store_learning(checkout, "L-att4", "Project row to push", "")
        dirty = get_dirty_entries(client_id="c", trw_dir=checkout)
        mark_synced(client_id="c", trw_dir=checkout, entries=dirty)
        after_push = get_dirty_entries(client_id="c", trw_dir=checkout)
        merged = SyncPuller("http://localhost:1", "k", client_id="c", trw_dir=checkout).merge_team_learnings(
            [{"source_learning_id": "R-att", "summary": "Pulled team tip", "impact": 0.6}]
        )

        monkeypatch.undo()
        pulled = asyncio.run(client.get("team-sync-R-att", _PINNED))

    assert "L-att4" in [entry.id for entry in dirty]
    assert {entry.namespace for entry in dirty} == {_PINNED}
    assert after_push == []
    assert (merged.inserted, merged.rejected) == (1, 0)
    assert (pulled["entry"]["content"], pulled["entry"]["remote_id"]) == ("Pulled team tip", "R-att")
    assert not list(checkout.rglob("memory.db"))


def test_single_entry_lookups_and_patches_go_through_the_daemon(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._memory_lookups import find_entry_by_id
    from trw_mcp.state.analytics.entries import mark_promoted
    from trw_mcp.tools._ceremony_reconcile_step import _live_tags
    from trw_mcp.tools._learning_helpers import _sync_merged_entry_to_backend

    with running_daemon(tmp_path / "userhome") as paths:
        token = mint_grant(paths, [_PINNED, USER_NAMESPACE])
        write_checkout_grant(checkout, token)
        _refuse_sqlite(monkeypatch)

        # The test config's trw_dir is absolute; resolve the merge path's .trw to this checkout.
        monkeypatch.setattr("trw_mcp.tools._learning_helpers._resolve_dedup_trw_dir", lambda _entries: checkout)
        store_learning(checkout, "L-att5", "Lookup summary", "", tags=["kept"])
        found = find_entry_by_id(checkout, "L-att5")
        tags = _live_tags(checkout, "L-att5")
        mark_promoted(checkout, "L-att5")
        _sync_merged_entry_to_backend(checkout / "learnings" / "entries", {"id": "L-att5", "detail": "merged"})

        monkeypatch.undo()
        row = asyncio.run(DaemonClient(token, paths=paths).get("L-att5", _PINNED))["entry"]

    assert found is not None and found["summary"] == "Lookup summary"
    assert tags is not None and "kept" in tags
    assert row["metadata"]["promoted_to_claude_md"] == "true"
    assert row["detail"] == "merged"
    assert not list(checkout.rglob("memory.db"))


def test_the_reconcile_step_lists_and_clears_pending_rows_over_the_daemon(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._constants import RECONCILE_PENDING_TAG
    from trw_mcp.tools._ceremony_reconcile_step import step_reconcile_local_writes

    with running_daemon(tmp_path / "userhome") as paths:
        token = mint_grant(paths, [_PINNED, USER_NAMESPACE])
        write_checkout_grant(checkout, token)
        _refuse_sqlite(monkeypatch)

        store_learning(checkout, "L-att6", "Written offline", "", tags=["kept", RECONCILE_PENDING_TAG])
        report = step_reconcile_local_writes(checkout)

        monkeypatch.undo()
        row = asyncio.run(DaemonClient(token, paths=paths).get("L-att6", _PINNED))["entry"]

    assert (report["pending"], report["learning_ids"], report["cleared"]) == (1, ["L-att6"], 1)
    assert row["tags"] == ["kept"]
    assert not list(checkout.rglob("memory.db"))


async def test_a_full_sync_pull_replays_into_the_daemon(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sync pull --full`` merges every page through the daemon and never opens the store file."""
    from tests._test_sync_client_support import _make_config
    from trw_mcp.sync._replay import run_full_pull
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    team = [
        {"source_learning_id": f"R-full{seq}", "summary": f"Team tip {seq}", "sync_seq": seq} for seq in range(1, 6)
    ]

    async def _page(*, since_seq: int, since_company_seq: int, **_: object) -> PullResult:
        page = [item for item in team if int(item["sync_seq"]) > since_seq][:2]
        return PullResult(team_learnings=page, status_code=200, next_company_seq=since_company_seq)

    with running_daemon(tmp_path / "userhome") as paths:
        token = mint_grant(paths, [_PINNED, USER_NAMESPACE])
        write_checkout_grant(checkout, token)
        _refuse_sqlite(monkeypatch)
        monkeypatch.setattr("trw_mcp.sync.client.resolve_sync_client_id", lambda: "replay-client")
        client = BackendSyncClient(_make_config(), checkout)
        monkeypatch.setattr(client._puller, "pull_intel_state", _page)

        report = await run_full_pull(client, receipt_path=checkout / "sync-replay.jsonl", resume=False, max_pages=10)

        monkeypatch.undo()
        daemon = DaemonClient(token, paths=paths)
        rows = [(await daemon.get(f"team-sync-R-full{seq}", _PINNED))["entry"] for seq in range(1, 6)]

    assert (report["status"], report["pulled"], report["merged"]) == ("completed", 5, 5)
    assert [(row["namespace"], row["remote_id"]) for row in rows] == [(_PINNED, f"R-full{seq}") for seq in range(1, 6)]
    assert not list(checkout.rglob("memory.db"))


_WRITER = """
import sqlite3, sys
from pathlib import Path

def _refuse(*_args, **_kwargs):
    raise AssertionError("a migrated checkout opened a SQLite connection")

sqlite3.connect = _refuse
import trw_memory.storage._connection as connection
connection.connect = _refuse

from trw_mcp.state.memory_adapter import store_learning

trw_dir, tag, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
for index in range(count):
    result = store_learning(trw_dir, f"L-{tag}-{index}", f"Concurrent {tag} row {index}", f"{tag} detail {index}")
    assert result["status"] == "recorded", result
"""


@pytest.mark.slow
def test_three_processes_write_concurrently_through_one_daemon(tmp_path: Path) -> None:
    """Three real trw-mcp processes, one daemon: all 600 rows land in their own namespaces, none hits a lock."""
    import os
    import subprocess
    import sys

    per_process = 200
    checkouts = {tag: f"project:attach-3333333{index}" for index, tag in enumerate("abc")}
    logs = {tag: tmp_path / f"writer-{tag}.log" for tag in checkouts}
    writers: list[subprocess.Popen[bytes]] = []
    with running_daemon(tmp_path / "userhome") as paths:
        try:
            for tag, namespace in checkouts.items():
                trw_dir = tmp_path / tag / ".trw"
                trw_dir.mkdir(parents=True)
                (trw_dir / "config.yaml").write_text(f"project_namespace: {namespace}\n", encoding="utf-8")
                write_checkout_grant(trw_dir, mint_grant(paths, [namespace, USER_NAMESPACE]))
                env = {**os.environ, "TRW_USER_DIR": str(tmp_path / "userhome"), "HOME": str(tmp_path / "home")}
                env["TRW_PROJECT_ROOT"] = str(tmp_path / tag)
                env.pop("TRW_PROJECT_NAMESPACE", None)
                with logs[tag].open("wb") as log:
                    writers.append(
                        subprocess.Popen(
                            [sys.executable, "-c", _WRITER, str(trw_dir), tag, str(per_process)],
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                    )
            for writer in writers:
                writer.wait(timeout=300)
        finally:
            # A timeout or a setup failure must not leave a writer running against the next test.
            for writer in writers:
                if writer.poll() is None:
                    writer.kill()
                writer.wait(timeout=30)
        outputs = [log.read_text(encoding="utf-8", errors="replace") for log in logs.values() if log.exists()]
        admin = mint_grant(paths, [*checkouts.values()])
        counts = {
            namespace: asyncio.run(DaemonClient(admin, paths=paths).status(namespace))["total_entries"]
            for namespace in checkouts.values()
        }

    assert [writer.returncode for writer in writers] == [0, 0, 0], outputs
    assert not [output for output in outputs if "database is locked" in output]
    assert counts == dict.fromkeys(checkouts.values(), per_process)
    assert not list(tmp_path.glob("[abc]/.trw/**/memory.db"))
