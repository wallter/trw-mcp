"""Tests for SyncPuller — PRD-INFRA-053.

PRD-FIX-087: pull_intel_state is now async + httpx.AsyncClient.
Tests are async and patch httpx.AsyncClient (with AsyncMock for
async context manager + awaitable get/post).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE, DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore


def _seed(
    store: FakeMemoryStore,
    entry_id: str,
    *,
    namespace: str = FAKE_NAMESPACE,
    content: str = "tip",
    detail: str = "",
    tags: list[str] | None = None,
    importance: float = 0.5,
    vector_clock: dict[str, int] | None = None,
    source: str = "human",
    metadata: dict[str, str] | None = None,
    remote_id: str | None = None,
    sync_seq: int = 1,
    synced: bool = True,
) -> MemoryEntry:
    """Seed a fully-formed row directly (the fake route's equivalent of ``backend.store`` +
    ``DeltaTracker.mark_synced``): pull.py reads ``last_synced_at``/``vector_clock`` straight off
    the entry, so those fields must be real, not just the fake's own dirty bookkeeping.
    """
    entry = MemoryEntry(
        id=entry_id,
        namespace=namespace,
        content=content,
        detail=detail,
        tags=list(tags or []),
        importance=importance,
        vector_clock=dict(vector_clock or {}),
        source=source,
        metadata=dict(metadata or {}),
        remote_id=remote_id,
        sync_seq=sync_seq,
        last_synced_at=datetime.now(timezone.utc) if synced else None,
    )
    store.rows[(namespace, entry_id)] = entry
    if synced:
        store.synced[(namespace, entry_id)] = sync_seq
    return entry


def _build_async_httpx_mock(response: object) -> MagicMock:
    """Build a mock AsyncClient that yields ``response`` from ``await client.get(...)``.

    Async context manager: ``async with httpx.AsyncClient(...) as client``
    requires __aenter__/__aexit__ on the class instance.
    """
    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.post = AsyncMock(return_value=response)
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_cls


def test_pull_result_model() -> None:
    """PullResult is a valid Pydantic model with expected defaults."""
    from trw_mcp.sync.pull import PullResult

    result = PullResult()
    assert result.state is None
    assert result.etag is None
    assert result.sync_hints is None
    assert result.team_learnings is None
    assert result.status_code == 0

    result2 = PullResult(
        state={"bandit_params": {"L-1": 1.2}},
        etag="abc123",
        sync_hints={"next_poll_recommended_at": "2026-04-06T12:00:00Z"},
        team_learnings=[{"id": "L-99", "summary": "team tip"}],
        status_code=200,
    )
    assert result2.state is not None
    assert result2.etag == "abc123"
    assert result2.status_code == 200
    assert len(result2.team_learnings) == 1  # type: ignore[arg-type]


async def test_pull_empty_returns_none() -> None:
    """Pull from unreachable URL returns None (fail-open)."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://unreachable.invalid:9999",
        api_key="test-key",
        timeout=1.0,
        client_id="sync-test",
    )
    result = await puller.pull_intel_state()
    assert result is None


async def test_puller_never_raises() -> None:
    """SyncPuller never raises exceptions on any error path."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://unreachable.invalid:9999",
        api_key="test-key",
        timeout=0.5,
        client_id="sync-test",
    )
    assert await puller.pull_intel_state(etag="stale-etag") is None
    assert await puller.pull_intel_state(since_seq=999) is None
    assert await puller.pull_intel_state(model_family="opus", trw_version="0.38.2") is None


def test_puller_constructor_strips_trailing_slash() -> None:
    """Backend URL has trailing slash stripped for clean path joining."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com/api/",
        api_key="test-key",
        client_id="sync-test",
    )
    assert puller._backend_url == "http://example.com/api"


def test_puller_default_timeout() -> None:
    """Default timeout is 5.0 seconds."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-test")
    assert puller._timeout == 5.0


def test_puller_warns_on_insecure_non_local_http_url() -> None:
    """Non-local plain HTTP backends emit an advisory security warning once."""
    from trw_mcp.sync.pull import SyncPuller

    with patch("trw_mcp.sync.pull.logger.warning") as mock_warning:
        SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-test")

    mock_warning.assert_called_once_with("sync_pull_insecure_url", url="http://example.com")


async def test_pull_sends_client_id_and_logs_structured_events() -> None:
    """Pull includes client_id/query params and emits start/complete events."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
    )

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "etag": "etag-1",
        "sync_hints": {"polling_cap_seconds": 60},
        "team_learnings": [{"source_learning_id": "remote-1"}],
        # PRD-INFRA-139 P1-B: server advertises the independent company cursor.
        "next_company_seq": 12,
    }
    mock_client_cls = _build_async_httpx_mock(response)

    with (
        patch("httpx.AsyncClient", mock_client_cls),
        patch("trw_mcp.sync.pull.logger.info") as mock_info,
    ):
        result = await puller.pull_intel_state(
            etag="cached-etag",
            since_seq=7,
            model_family="opus",
            trw_version="v1",
            since_company_seq=4,
        )

    assert result is not None
    # P1-B: the independent company cursor round-trips on request and response.
    assert result.next_company_seq == 12
    mock_client = mock_client_cls.return_value.__aenter__.return_value
    _, kwargs = mock_client.get.call_args
    assert kwargs["headers"]["If-None-Match"] == '"cached-etag"'
    assert kwargs["params"] == {
        "since_seq": 7,
        "since_company_seq": 4,
        "client_id": "sync-client-1",
        "model_family": "opus",
        "trw_version": "v1",
    }
    assert mock_info.call_args_list[0].kwargs["event_type"] == "sync_pull_start"
    assert mock_info.call_args_list[1].kwargs["event_type"] == "sync_pull_complete"
    assert mock_info.call_args_list[1].kwargs["team_learnings_count"] == 1


async def test_pull_malformed_200_payload_returns_none() -> None:
    """Malformed 200 pull payloads fail open instead of looking successful."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    malformed_payloads = [
        {},
        {"etag": None, "sync_hints": {}, "team_learnings": []},
        {"etag": "etag-1", "sync_hints": [], "team_learnings": []},
        {"etag": "etag-1", "sync_hints": {}, "team_learnings": [1]},
    ]

    for payload in malformed_payloads:
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        mock_client_cls = _build_async_httpx_mock(response)

        with patch("httpx.AsyncClient", mock_client_cls):
            assert await puller.pull_intel_state(since_seq=7) is None


async def test_pull_boundary_failure_logs_structured_warning() -> None:
    """Boundary failures log structured warning + traceback and still fail open."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    mock_client_cls = _build_async_httpx_mock(MagicMock())
    mock_client = mock_client_cls.return_value.__aenter__.return_value
    mock_client.get = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("httpx.AsyncClient", mock_client_cls),
        patch("trw_mcp.sync.pull.logger.warning") as mock_warning,
    ):
        assert await puller.pull_intel_state(since_seq=3) is None

    args, kwargs = mock_warning.call_args
    assert args == ("sync_pull_error",)
    assert kwargs["event_type"] == "sync_pull_error"
    assert kwargs["error_type"] == "RuntimeError"
    assert kwargs["since_seq"] == 3
    assert kwargs["client_id"] == "sync-client-1"
    assert kwargs["exc_info"] is True


async def test_pull_not_modified_returns_distinct_result() -> None:
    """304 responses are distinguishable from transport failures."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    response = MagicMock()
    response.status_code = 304
    mock_client_cls = _build_async_httpx_mock(response)

    with patch("httpx.AsyncClient", mock_client_cls):
        result = await puller.pull_intel_state(etag="etag-1", since_seq=7)

    assert result is not None
    assert result.not_modified is True
    assert result.status_code == 304


def test_sync_modules_follow_structlog_conventions() -> None:
    """Sync modules keep the established structlog naming/keyword conventions."""
    import trw_mcp.sync.cache as cache_module
    import trw_mcp.sync.client as client_module
    import trw_mcp.sync.pull as pull_module

    for module in (cache_module, client_module, pull_module):
        source = inspect.getsource(module)
        assert "structlog.get_logger(__name__)" in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
                continue
            if node.func.attr not in {"debug", "info", "warning", "exception"}:
                continue
            assert all(keyword.arg != "event" for keyword in node.keywords)
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                event_name = node.args[0].value
                assert event_name == event_name.lower()
                assert "-" not in event_name


def test_merge_team_learnings_inserts_team_sync_entries(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """Pulled team learnings are inserted locally with attribution metadata."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    merged = puller.merge_team_learnings(
        [
            {
                "source_learning_id": "remote-1",
                "summary": "shared tip",
                "detail": "backend detail",
                "impact": 0.8,
                "tags": ["sync"],
                "type": "pattern",
                "status": "active",
                "sync_seq": 7,
                "vector_clock": {"remote-a": 2},
                "metadata": {"origin": "backend"},
            }
        ]
    )

    assert merged.applied == 1
    stored = fake_memory_store.get("team-sync-remote-1")
    assert stored is not None
    assert stored.source == "team_sync"
    assert stored.remote_id == "remote-1"
    assert stored.metadata["origin"] == "backend"
    assert stored.metadata["team_sync_pull_seq"] == "7"
    assert stored.vector_clock == {"remote-a": 2}
    assert fake_memory_store.page_dirty(FAKE_NAMESPACE, 100) == []


def test_re_pulling_an_applied_revision_changes_nothing(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """The same remote revision offered again (equal vector clock) is not merged a second time."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="c", trw_dir=tmp_path)
    learning = {"source_learning_id": "remote-9", "summary": "tip", "detail": "d", "vector_clock": {"peer": 3}}

    first = puller.merge_team_learnings([learning])
    before = fake_memory_store.get("team-sync-remote-9")
    again = puller.merge_team_learnings([dict(learning, sync_seq=11)])
    after = fake_memory_store.get("team-sync-remote-9")

    assert (first.inserted, again.unchanged, again.applied, again.rejected) == (1, 1, 0, 0)
    assert before is not None and after is not None
    assert (after.outcome_history, after.detail, after.sync_seq) == (before.outcome_history, "d", before.sync_seq)


def test_a_pull_older_than_an_unpushed_local_edit_leaves_the_edit_to_push(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """push -> local edit -> pull of the pushed revision: the edit must still reach the next push."""
    from trw_mcp.sync.pull import SyncPuller

    # The push landed vector_clock {"me": 1}; the local edit that followed bumped
    # the clock but has not been pushed (last_synced_at is None).
    _seed(fake_memory_store, "L-own", detail="v2", vector_clock={"me": 2}, sync_seq=2, synced=False)
    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="me", trw_dir=tmp_path)

    merged = puller.merge_team_learnings(
        [{"source_learning_id": "L-own", "summary": "tip", "detail": "v1", "vector_clock": {"me": 1}}]
    )

    assert (merged.unchanged, merged.applied) == (1, 0)
    assert [(e.id, e.detail) for e in fake_memory_store.page_dirty(FAKE_NAMESPACE, 100)] == [("L-own", "v2")]


def test_merge_team_learnings_resolves_conflicts(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """Existing pulled entries are merged with vector-clock conflict resolution."""
    from trw_mcp.sync.pull import SyncPuller

    _seed(
        fake_memory_store,
        "team-sync-remote-1",
        remote_id="remote-1",
        content="shared tip",
        detail="local detail",
        tags=["local"],
        importance=0.4,
        vector_clock={"local-a": 1},
        source="team_sync",
        metadata={"team_sync_pull_seq": "5"},
    )

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    merged = puller.merge_team_learnings(
        [
            {
                "source_learning_id": "remote-1",
                "summary": "shared tip",
                "detail": "remote detail",
                "impact": 0.9,
                "tags": ["remote"],
                "type": "pattern",
                "status": "active",
                "sync_seq": 9,
                "vector_clock": {"remote-b": 1},
                "metadata": {"origin": "backend"},
            }
        ]
    )

    assert merged.applied == 1
    stored = fake_memory_store.get("team-sync-remote-1")
    assert stored is not None
    assert stored.source == "team_sync"
    assert "local detail" in stored.detail
    assert "remote detail" in stored.detail
    assert stored.tags == ["local", "remote"]
    assert stored.importance == 0.9
    assert stored.metadata["team_sync_pull_seq"] == "9"
    # The merge holds local content the server lacks, so the next push carries it.
    assert [(e.id, e.detail) for e in fake_memory_store.page_dirty(FAKE_NAMESPACE, 100)] == [
        ("team-sync-remote-1", stored.detail)
    ]


def test_an_unpushed_local_edit_survives_a_teammates_newer_revision(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """push -> local edit (no clock tick) -> pull of a teammate's edit: both reach the next push."""
    from trw_mcp.sync.pull import SyncPuller

    # The pushed revision carried vector_clock {"me": 1}; the local edit that followed
    # changed content without ticking the clock (an un-pushed edit, per real `update()`).
    _seed(fake_memory_store, "L-mine", detail="my edit", vector_clock={"me": 1}, sync_seq=2, synced=False)
    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="me", trw_dir=tmp_path)

    puller.merge_team_learnings(
        [
            {
                "source_learning_id": "L-mine",
                "summary": "tip",
                "detail": "their edit",
                "vector_clock": {"me": 1, "b": 1},
            }
        ]
    )

    dirty = fake_memory_store.page_dirty(FAKE_NAMESPACE, 100)
    assert [e.id for e in dirty] == ["L-mine"]
    assert "my edit" in dirty[0].detail and "their edit" in dirty[0].detail


def test_a_pulled_revision_never_lowers_the_local_write_counter(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """The next local edit must carry a counter above every one this client already pushed for the entry."""
    from trw_mcp.sync.pull import SyncPuller

    # Five local writes (initial store + 4 edits), all pushed: sync_seq=5, synced.
    pushed = _seed(fake_memory_store, "L-seq", detail="v5", vector_clock={"me": 1}, sync_seq=5, synced=True)
    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="me", trw_dir=tmp_path)

    puller.merge_team_learnings(
        [{"source_learning_id": "L-seq", "summary": "tip", "detail": "theirs", "vector_clock": {"me": 1, "b": 1}}]
    )
    pulled = fake_memory_store.get("L-seq")

    assert pulled is not None and pulled.detail == "theirs"
    assert pulled.sync_seq > pushed.sync_seq
    # The teammate's revision is what the server holds, so it lands clean.
    assert fake_memory_store.page_dirty(FAKE_NAMESPACE, 100) == []


def test_merge_company_sync_learnings_tagged_distinctly(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """PRD-INFRA-139 FR06: company-tier learnings tagged source=company_sync in
    metadata are merged via the same team-learnings path but stored with the
    company_sync source so they stay distinguishable from team learnings."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    merged = puller.merge_team_learnings(
        [
            {
                "source_learning_id": "company-1",
                "summary": "company-wide lesson",
                "detail": "portable detail",
                "impact": 0.9,
                "tags": ["sync"],
                "type": "pattern",
                "status": "active",
                "sync_seq": 3,
                "vector_clock": {},
                # Server tags company-tier learnings distinctly.
                "metadata": {"source": "company_sync"},
            }
        ]
    )

    assert merged.applied == 1
    stored = fake_memory_store.get("team-sync-company-1")
    assert stored is not None
    assert stored.source == "company_sync"
    assert stored.metadata["source"] == "company_sync"
    assert stored.remote_id == "company-1"


def test_two_peers_sharing_a_source_learning_id_stay_two_rows(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """PRD-CORE-245 FR03: the pull path resolves an existing row WITHIN its namespace.

    ``_local_team_learning_id`` mints the local id from a PEER-SUPPLIED string, so
    before the namespace predicate two peers emitting the same
    ``source_learning_id`` into two namespaces matched each other's row and the
    merge collapsed them into one.
    """
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )
    payload = {
        "source_learning_id": "remote-collide",
        "summary": "peer content",
        "impact": 0.5,
        "type": "pattern",
        "status": "active",
    }

    assert puller.merge_team_learnings([dict(payload)], namespace="project:alpha").applied == 1
    assert puller.merge_team_learnings([dict(payload)], namespace="project:beta").applied == 1

    namespaces = sorted(ns for (ns, entry_id) in fake_memory_store.rows if entry_id == "team-sync-remote-collide")
    assert namespaces == ["project:alpha", "project:beta"]


def test_pulled_entry_lands_in_the_named_namespace_with_the_peers_clock(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """PRD-CORE-245 FR08: built through the factory, but the PEER's clock survives.

    A deserialiser of remote state must reproduce the causality the payload
    carries; stamping a local clock over it is the same corruption FR08 exists to
    prevent, only inverted.
    """
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    merged = puller.merge_team_learnings(
        [
            {
                "source_learning_id": "remote-ns",
                "summary": "namespaced tip",
                "impact": 0.5,
                "type": "pattern",
                "status": "active",
                "vector_clock": {"peer-node": 4},
            }
        ],
        namespace="project:alpha",
    )

    assert merged.applied == 1
    stored = fake_memory_store.rows.get(("project:alpha", "team-sync-remote-ns"))
    assert stored is not None, "the pulled entry must land in the namespace the caller named"
    assert stored.namespace == "project:alpha"
    assert stored.vector_clock == {"peer-node": 4}
    assert ("default", "team-sync-remote-ns") not in fake_memory_store.rows


def test_merge_team_learnings_books_a_security_refusal_as_blocked_not_failed(
    daemon_checkout: DaemonCheckout,
) -> None:
    """PRD-FIX-138-FR01: a write-time PoisoningError is a judged decision.

    Booked as ``failed`` it held the pull cursor on the item forever (the cycle
    treats ``failed`` as "never judged"); one poisoned team learning then stalled
    sync for the whole install — observed 2026-09-16 with 399 of 1330 rows pulled.

    This needs the REAL write gate (the fake store's ``apply_synced`` runs no gate
    at all), so it routes through the daemon rather than ``fake_memory_store``.
    """
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=daemon_checkout.trw_dir,
    )
    poisoned = {
        "source_learning_id": "remote-poison",
        "summary": "retry wrapper",
        "detail": "the harness calls eval(user_input) before dispatch",
        "impact": 0.7,
        "tags": ["sync"],
        "type": "pattern",
        "status": "active",
        "sync_seq": 9,
        "vector_clock": {},
        "metadata": {},
    }
    clean = dict(poisoned, source_learning_id="remote-clean", detail="plain detail", sync_seq=10)

    result = puller.merge_team_learnings([poisoned, clean])

    assert result.blocked == 1
    assert result.failed == 0
    assert result.applied == 1
    assert result.rejected == 1
    assert result.status == "partial"
    poisoned_row = asyncio.run(daemon_checkout.client.get("team-sync-remote-poison", daemon_checkout.namespace))
    assert poisoned_row["status"] == "not_found"
    clean_row = asyncio.run(daemon_checkout.client.get("team-sync-remote-clean", daemon_checkout.namespace))
    assert clean_row["status"] == "ok"
    assert result.as_log_fields()["blocked"] == 1
