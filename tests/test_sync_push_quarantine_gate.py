"""PRD-CORE-333: sync push asks the quarantine ledger immediately before every POST (CORE-333-PUBLISHER-BYPASS).

A row quarantined after its page was read must not be sent. The push stops before the
batch that holds it instead of dropping the row, because the sync cycle acknowledges by
count as a prefix of the page; the next page leaves the blocked row out, so the push
cannot stall on it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from trw_memory.integrations._backend import create_backend_from_config
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryEntry
from trw_memory.security.quarantine_ledger import LedgerIdentity, QuarantineLedger, ledger_for_config
from trw_memory.sync.delta import DeltaTracker

pytestmark = pytest.mark.usefixtures("governing_project")


@pytest.fixture(autouse=True)
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "user-base"
    base.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(base))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return base


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._real = httpx.AsyncClient

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entries = json.loads(request.content).get("entries", [])
        return httpx.Response(200, json={"inserted": len(entries), "updated": 0, "skipped": 0, "errors": 0})

    def __call__(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(self._handle)
        return self._real(*args, **kwargs)

    def sent_ids(self) -> list[str]:
        return [e["source_learning_id"] for r in self.requests for e in json.loads(r.content).get("entries", [])]


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    rec = _Recorder()
    with patch("httpx.AsyncClient", rec):
        yield rec


def _pusher(trw_dir: Path, batch_size: int = 1) -> Any:
    from trw_mcp.sync.push import SyncPusher

    return SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="q",
        batch_size=batch_size,
        learning_sharing_enabled=True,
        source_trw_dir=trw_dir,
    )


def _row(entry_id: str) -> MemoryEntry:
    return MemoryEntry(id=entry_id, namespace="default", content=f"content {entry_id}", sync_seq=1, sync_hash="a" * 64)


async def test_the_push_stops_before_a_batch_holding_a_quarantined_row(
    governing_project: Path, recorder: _Recorder
) -> None:
    ledger_for_config(MemoryConfig()).append(LedgerIdentity.of(_row("Q-1")), "quarantined", actor="system")

    result = await _pusher(governing_project / ".trw").push_learnings([_row("V-1"), _row("Q-1"), _row("V-2")])

    assert recorder.sent_ids() == ["V-1"], "only the prefix before the blocked row is sent"
    assert result.pushed == 1


async def test_an_unreadable_ledger_sends_nothing(
    governing_project: Path, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(self: QuarantineLedger) -> object:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(QuarantineLedger, "view", _broken)

    result = await _pusher(governing_project / ".trw").push_learnings([_row("V-1")])

    assert recorder.requests == [] and result.pushed == 0


async def test_a_row_quarantined_after_paging_never_stalls_the_push(
    governing_project: Path, recorder: _Recorder, tmp_path: Path
) -> None:
    config = MemoryConfig(storage_path=str(tmp_path / "memory"))
    with create_backend_from_config(config, "default") as backend:
        for entry_id in ("V-1", "V-2", "V-3"):
            backend.store(MemoryEntry(id=entry_id, namespace="default", content=f"content {entry_id}"))
        pusher = _pusher(governing_project / ".trw", batch_size=10)

        page = DeltaTracker.get_dirty_entries(backend, namespace="default", limit=10)
        for path in {ledger_for_config(config).path, ledger_for_config(MemoryConfig()).path}:
            QuarantineLedger(path).append(LedgerIdentity.of(page[1]), "quarantined", actor="system")
        first = await pusher.push_learnings(page)  # quarantined between paging and the POST
        DeltaTracker.mark_synced([e.id for e in page[: first.pushed]], backend, namespace="default")

        page = DeltaTracker.get_dirty_entries(backend, namespace="default", limit=10)
        second = await pusher.push_learnings(page)
        DeltaTracker.mark_synced([e.id for e in page[: second.pushed]], backend, namespace="default")

    assert first.pushed == 0, "the cycle that paged the row stops before its batch"
    assert sorted(recorder.sent_ids()) == ["V-1", "V-3"], "the next page leaves it out and the rest are pushed"
