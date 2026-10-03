"""PRD-SEC-023 FR05 (chokepoint C3, and C5's YAML sink): no row labelled above ``team`` leaves the host or lands in a tracked file.

Four row sinks, each asked through ``LabelPolicy.admit(rows, Sink.PLATFORM | Sink.PROJECT_FILES)``:

* the sync push sends only rows at or below ``team``; a withheld row is marked synced locally (so it never blocks the oldest-first
  500-row dirty page) and counted (``PushResult.withheld_by_label``);
* a memory publish refuses such a row with ``retryable=False``, so it is never queued for a retry either;
* ``backup create`` refuses the remote upload, before the presign request, while the store holds any such row, prints the count (never the
  content) and keeps the local archive;
* ``trw_learn`` writes no YAML sidecar into the project checkout for such a row.

Transports are ``httpx.MockTransport`` or a recording client; no daemon and no network.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from trw_memory.labels import Level
from trw_memory.models.memory import MemoryEntry

from tests._memory_store_fake import FakeMemoryStore
from tests._test_sync_client_support import _acquired_lock, _make_config
from trw_mcp.state._session_mark import session_mark

pytestmark = pytest.mark.usefixtures("governing_project")


@pytest.fixture(autouse=True)
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "user-base"
    base.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(base))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    labels = base / "labels.yaml"
    labels.write_text("version: 1\nrules:\n  - tags_any: [finance]\n    level: personal\n", encoding="utf-8")
    labels.chmod(0o600)
    return base


def _row(
    entry_id: str, *, seq: int = 1, tags: list[str] | None = None, namespace: str = "default", **metadata: str
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        content=f"content {entry_id}",
        namespace=namespace,
        tags=tags or [],
        metadata=dict(metadata),
        sync_seq=seq,
        sync_hash="a" * 64,
    )


class _Recorder:
    """An ``httpx.AsyncClient`` factory over a ``MockTransport`` that records every request and answers like the sync API."""

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


# ── sync push ────────────────────────────────────────────────────────────────


def _pusher(trw_dir: Path) -> Any:
    from trw_mcp.sync.push import SyncPusher

    return SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="labels",
        learning_sharing_enabled=True,
        source_trw_dir=trw_dir,
    )


async def test_push_sends_only_team_rows_and_counts_the_withheld(governing_project: Path, recorder: _Recorder) -> None:
    rows = [
        _row("L-stamp", trw_label="personal"),
        _row("L-team"),
        _row("L-tag", tags=["Finance"]),
        _row("L-ns", namespace="user:alice"),
    ]

    result = await _pusher(governing_project / ".trw").push_learnings(rows)

    assert recorder.sent_ids() == ["L-team"]
    assert result.pushed == 1 and result.withheld_by_label == 3


async def test_push_of_only_labelled_rows_makes_no_request(governing_project: Path, recorder: _Recorder) -> None:
    result = await _pusher(governing_project / ".trw").push_learnings([_row("L-1", trw_label="sensitive")])

    assert recorder.requests == []
    assert result.withheld_by_label == 1 and result.pushed == 0


class _DirtyStore:
    """The local store's dirty set, paged oldest-first DIRTY_PAGE_SIZE rows at a time, like ``page_dirty``."""

    def __init__(self, rows: list[MemoryEntry]) -> None:
        self.dirty = {row.id: row for row in rows}

    def page(self) -> list[MemoryEntry]:
        from trw_mcp.sync._client_runtime import DIRTY_PAGE_SIZE

        return sorted(self.dirty.values(), key=lambda r: r.sync_seq)[:DIRTY_PAGE_SIZE]

    def mark_synced(self, entries: list[MemoryEntry]) -> None:
        for entry in entries:
            self.dirty.pop(entry.id, None)


def _client(trw_dir: Path, store: _DirtyStore) -> Any:
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="labels"):
        client = BackendSyncClient(_make_config(platform_telemetry_enabled=False), trw_dir)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.side_effect = lambda: _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_company_pull_seq.return_value = 0
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(state={}, etag=None, team_learnings=[], sync_hints={}, status_code=200)
    )
    client._cache = MagicMock()
    client._get_dirty_entries = store.page
    client._mark_synced = store.mark_synced
    return client


async def test_599_personal_rows_ahead_of_one_team_row_push_exactly_the_team_row_within_two_cycles(
    governing_project: Path, recorder: _Recorder
) -> None:
    """FR05's pass condition: withheld rows are marked synced, so they never pin the oldest-first 500-row page."""
    rows = [_row(f"L-p{i:03d}", seq=i, trw_label="personal") for i in range(599)] + [_row("L-team", seq=599)]
    store = _DirtyStore(rows)
    client = _client(governing_project / ".trw", store)

    await client._run_one_cycle(force=True)
    await client._run_one_cycle(force=True)

    assert recorder.sent_ids() == ["L-team"], "no labelled row was sent, and the team row behind them was"
    assert store.dirty == {}, "every withheld row is marked synced locally"


def test_split_by_label_withholds_exactly_the_rows_a_rule_matches(user_dir: Path) -> None:
    """The 500-row page the timing test below measures splits correctly: rules t0..t49 withhold, the rest pass."""
    from trw_mcp.sync.push import split_by_label

    rules = "".join(f"  - tags_any: [t{i}]\n    level: personal\n" for i in range(50))
    (user_dir / "labels.yaml").write_text("version: 1\nrules:\n" + rules, encoding="utf-8")
    rows = [
        _row(f"L-{i}", seq=i, tags=[f"t{i % 80}", "code"], namespace=("default", "user:local")[i % 2])
        for i in range(500)
    ]

    admitted, withheld = split_by_label(rows)

    assert [r.id for r in withheld] == [r.id for i, r in enumerate(rows) if i % 80 < 50]
    assert [r.id for r in admitted] == [r.id for i, r in enumerate(rows) if i % 80 >= 50]


@pytest.mark.requires_local_timing
def test_withholding_labelled_rows_from_a_full_dirty_page_is_fast(user_dir: Path) -> None:
    """The cycle splits every 500-row dirty page: within the admit budget (p95 <= 2 ms, 500 rows, 50 rules), best of 3 x 200 runs."""
    import statistics
    import time

    from tests._timing import assert_budget
    from trw_mcp.sync.push import split_by_label

    rules = "".join(f"  - tags_any: [t{i}]\n    level: personal\n" for i in range(50))
    (user_dir / "labels.yaml").write_text("version: 1\nrules:\n" + rules, encoding="utf-8")
    rows = [
        _row(f"L-{i}", seq=i, tags=[f"t{i % 80}", "code"], namespace=("default", "user:local")[i % 2])
        for i in range(500)
    ]
    best = float("inf")
    for _ in range(3):
        for _ in range(20):
            split_by_label(rows)
        samples = []
        for _ in range(200):
            start = time.perf_counter()
            split_by_label(rows)
            samples.append((time.perf_counter() - start) * 1000)
        best = min(best, statistics.quantiles(samples, n=20)[-1])

    assert_budget("labels.split_by_label p95, 500-row dirty page, 50 rules", best, 2.0, "ms")


# ── memory publish ───────────────────────────────────────────────────────────


def _publish(entry: MemoryEntry, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, object], list[httpx.Request]]:
    from trw_memory.models.config import MemoryConfig
    from trw_memory.sync import _remote_publish

    calls: list[httpx.Request] = []
    real = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(201, json={"id": "R-1"})
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(_remote_publish, "platform_contact_blocked", lambda _cfg, _op: None)
    monkeypatch.setattr(httpx, "Client", factory)
    cfg = MemoryConfig(platform_url="https://api.trwframework.com", platform_api_key="k", sync_enabled=True)
    return dict(_remote_publish.publish_memory_result(entry.model_copy(update={"importance": 0.9}), cfg)), calls


def test_publish_of_a_team_row_posts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the refusal below."""
    result, calls = _publish(_row("L-team"), monkeypatch)

    assert result["success"] is True and len(calls) == 1


@pytest.mark.parametrize(
    "row", [_row("L-stamp", trw_label="personal"), _row("L-tag", tags=["finance"]), _row("L-ns", namespace="user:bob")]
)
def test_publish_refuses_a_labelled_row_without_retry(row: MemoryEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _publish(row, monkeypatch)

    assert calls == []
    assert result == {"success": False, "remote_id": None, "retryable": False}


@pytest.mark.parametrize(("tags", "posted"), [(["ops"], True), (["ops", "Finance"], False)])
def test_the_sidecar_publisher_sends_no_row_a_rule_labels_above_team(
    tags: list[str], posted: bool, governing_project: Path
) -> None:
    """A YAML sidecar written before one of its tags got a rule is still not published (its tags are judged at the send)."""
    from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
    from trw_mcp.telemetry.publisher import publish_learnings

    trw_dir = governing_project / ".trw"
    _write_learning(trw_dir / "learnings" / "entries", "L-side.yaml", _make_learning(impact=0.9, tags=tags))
    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=_make_config(learning_sharing_enabled=True)),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning", return_value=True) as post,
    ):
        result = publish_learnings()

    assert post.called is posted
    assert result["published"] == int(posted)


# ── remote backup ────────────────────────────────────────────────────────────

_PROJECT = "project:demo-1a2b3c4d"


def _backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[MemoryEntry]) -> list[httpx.Request]:
    from trw_memory.daemon import DiscoveryAbsent
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    backend = SQLiteBackend(db_path)
    for row in rows:
        backend.store(row.model_copy(update={"namespace": _PROJECT}) if row.namespace == "default" else row)
    backend.close()
    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda paths: DiscoveryAbsent(reason="none"))
    monkeypatch.setattr("trw_mcp.server._backup_remote_scope.invoking_namespace", lambda: _PROJECT)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        _subcommands_backup,
        "_load_config",
        lambda: TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k", backup_remote_enabled=True),
    )
    calls: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(
                200, json={"url": "https://bucket.s3.amazonaws.com/k?sig=1", "key": "backups/k.db.gz"}
            )
        return httpx.Response(200)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    _subcommands_backup.run_backup(argparse.Namespace(backup_command="create", namespace="default", db=str(db_path)))
    return calls


@pytest.mark.integration
def test_backup_of_a_team_only_store_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the refusal below."""
    calls = _backup(tmp_path, monkeypatch, [_row("L-team")])

    assert [c.url.path for c in calls][:1] == ["/v1/backup/presign"]
    assert "Uploaded to remote backup" in capsys.readouterr().out


@pytest.mark.integration
def test_backup_refuses_the_upload_before_presign_while_the_store_holds_labelled_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _backup(
        tmp_path, monkeypatch, [_row("L-team"), _row("L-stamp", trw_label="personal"), _row("L-tag", tags=["finance"])]
    )

    out = capsys.readouterr().out
    assert calls == [], "not even the presign request"
    assert "Created backup archive:" in out and list(tmp_path.rglob("*.db.gz")), "the local archive is kept"
    assert "Remote upload refused" in out and "2 rows labelled above team" in out
    assert "content L-stamp" not in out and "finance" not in out, "a count, never content or a tag"


# ── the YAML sidecar in the checkout ─────────────────────────────────────────


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeMemoryStore:
    fake = FakeMemoryStore()
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (fake, "default"))
    (tmp_path / ".trw" / "memory").mkdir(parents=True, exist_ok=True)
    return fake


def _learn_and_save(tmp_path: Path, learning_id: str, tags: list[str]) -> tuple[Path | str, list[Any]]:
    from trw_mcp.state.memory_adapter import store_learning
    from trw_mcp.tools._learn_side_effects import _save_yaml_backup
    from trw_mcp.tools._learning_helpers import LearningParams

    trw_dir = tmp_path / ".trw"
    stored = store_learning(trw_dir, learning_id, f"summary {learning_id}", "detail", tags=tags)
    saved: list[Any] = []
    path = _save_yaml_backup(
        LearningParams(
            summary=f"summary {learning_id}",
            detail="detail",
            learning_id=learning_id,
            tags=tags,
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="",
        ),
        consolidated_from=None,
        trw_dir=trw_dir,
        entries_dir=trw_dir / "learnings" / "entries",
        save_entry_fn=lambda _d, entry: saved.append(entry) or trw_dir / f"{entry.id}.yaml",
        update_analytics_fn=lambda *_a: None,
        scope="auto",
        store_result=stored,
    )
    return path, saved


def test_a_team_row_gets_its_yaml_sidecar(fake_store: FakeMemoryStore, tmp_path: Path) -> None:
    """The control for the two cases below."""
    path, saved = _learn_and_save(tmp_path, "L-team", ["ops"])

    assert [e.id for e in saved] == ["L-team"] and str(path).endswith("L-team.yaml")


def test_a_row_a_rule_labels_personal_gets_no_yaml_sidecar(fake_store: FakeMemoryStore, tmp_path: Path) -> None:
    path, saved = _learn_and_save(tmp_path, "L-fin", ["finance"])

    assert saved == []
    assert str(path).startswith("sqlite://"), "the store's own locator, as for a user-tier row"


def test_a_row_written_while_the_mark_is_personal_gets_no_yaml_sidecar(
    fake_store: FakeMemoryStore, tmp_path: Path
) -> None:
    session_mark().raise_to(Level.PERSONAL)

    _path, saved = _learn_and_save(tmp_path, "L-after", ["ops"])

    assert saved == []
