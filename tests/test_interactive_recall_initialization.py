"""Interactive recall must not join a cold model load silently."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance

from tests._tools_learning_shared import _get_tools, set_project_root  # noqa: F401
from trw_mcp.models.config import get_config
from trw_mcp.state import _memory_connection
from trw_mcp.tools import learning
from trw_mcp.tools._interactive_recall import prepare_interactive_recall


def test_registered_recall_defers_cold_model_and_reports_semantic_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_config(), "embeddings_enabled", True)
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    scheduled: list[bool] = []
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", lambda: scheduled.append(True))

    def adapter(*args: object, allow_cold_embedding_init: bool = True, **kwargs: object) -> list[dict[str, object]]:
        assert allow_cold_embedding_init is False, "Interactive caller would wait on model initialization"
        return []

    monkeypatch.setattr(learning, "adapter_recall", adapter)
    result = _get_tools()["trw_recall"].fn(query="reusable queue failure", max_results=3)
    assert "semantic coverage" in result["retrieval_warning"]
    assert scheduled == [True]


@pytest.mark.parametrize("query,enabled", [("*", True), ("", True), ("queue", False)])
def test_no_warmup_or_warning_for_wildcard_or_disabled(
    monkeypatch: pytest.MonkeyPatch, query: str, enabled: bool
) -> None:
    warmup = MagicMock()
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", warmup)
    adapter = MagicMock(return_value=[])
    prepared, warning = prepare_interactive_recall(adapter, embeddings_enabled=enabled, query=query)
    assert prepared(query=query) == []
    assert adapter.call_args.kwargs["allow_cold_embedding_init"] is False
    assert warning is None
    warmup.assert_not_called()


def test_reviewer_does_not_schedule_model_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr("trw_mcp.state._surface_role.reviewer_role_active", lambda: True)
    warmup = MagicMock()
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", warmup)
    _, warning = prepare_interactive_recall(MagicMock(return_value=[]), embeddings_enabled=True, query="queue")
    assert warning is not None
    warmup.assert_not_called()


def test_ready_embedder_still_reaches_hybrid_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.state._memory_queries import _search_entries

    provider = MagicMock()
    provider.embed.return_value = [0.5]
    space = EmbeddingSpace("a" * 64, "ready-fixture-v1", 1)
    provider.embedding_space.return_value = space
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: provider)
    cold = MagicMock(side_effect=AssertionError("Cold initializer reached"))
    monkeypatch.setattr(_memory_connection, "get_embedder", cold)
    entry = MemoryEntry(id="L-ready", content="Ready semantic model")
    backend = MagicMock()
    backend.list_entries.return_value = [entry]
    backend.get_vector_records.return_value = {
        entry.id: StoredVector((0.5,), VectorProvenance.for_vector(space, f"{entry.content} {entry.detail}", [0.5]))
    }
    hybrid = MagicMock(return_value=[entry])
    monkeypatch.setattr("trw_memory.retrieval.pipeline.hybrid_search", hybrid)
    prepared, warning = prepare_interactive_recall(_search_entries, embeddings_enabled=True, query="queue")
    assert prepared(backend, "queue") == [entry]
    assert warning is None
    hybrid.assert_called_once()
    backend.get_vector_records.assert_called_once_with([entry.id], namespace=entry.namespace)
    cold.assert_not_called()


def test_warmup_failure_does_not_hide_available_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", MagicMock(side_effect=RuntimeError("thread")))
    prepared, warning = prepare_interactive_recall(MagicMock(return_value=[]), embeddings_enabled=True, query="queue")
    assert prepared() == []
    assert warning is not None


def test_registered_recall_finishes_while_initializer_lock_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._memory_queries import _search_entries

    monkeypatch.setattr(get_config(), "embeddings_enabled", True)
    monkeypatch.setattr(get_config(), "nudge_enabled", False)
    monkeypatch.setattr(_memory_connection, "_embedder_checked", False)
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", lambda: False)
    backend = MagicMock()
    backend.search.return_value = []

    def adapter(*args: object, query: str, allow_cold_embedding_init: bool = True, **kwargs: object) -> list:
        return _search_entries(backend, query, allow_cold_embedding_init=allow_cold_embedding_init)

    monkeypatch.setattr(learning, "adapter_recall", adapter)
    recall = _get_tools()["trw_recall"].fn
    finished = threading.Event()
    results: list[object] = []

    def call() -> None:
        try:
            results.append(recall(query="queue retry", max_results=3))
        except Exception as exc:
            results.append(exc)
        finally:
            finished.set()

    _memory_connection._embedder_lock.acquire()
    worker = threading.Thread(target=call, daemon=True)
    try:
        worker.start()
        completed_before_release = finished.wait(2)
    finally:
        _memory_connection._embedder_lock.release()
        worker.join(2)
    assert completed_before_release, "Public recall waited for the initializer lock"
    assert isinstance(results[0], dict), results
    assert "retrieval_warning" in results[0]


def test_production_adapter_returns_useful_hits_during_single_flight_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real registered tool, SQLite adapter and scheduler; only model load is controlled."""
    tools = _get_tools()
    cfg = get_config()
    monkeypatch.setattr(cfg, "embeddings_enabled", False)
    monkeypatch.setattr(cfg, "nudge_enabled", False)
    captured = tools["trw_learn"].fn(summary="Retryworker bounds delivery retries", scope="project")
    monkeypatch.setattr(cfg, "embeddings_enabled", True)
    monkeypatch.delenv("TRW_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(_memory_connection, "_embedder_checked", False)
    monkeypatch.setattr(_memory_connection, "_embedder", None)
    monkeypatch.setattr(_memory_connection, "_WARMUP_THREAD", None)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def controlled_initializer() -> None:
        calls.append(threading.current_thread().name)
        entered.set()
        assert threading.current_thread().name == "trw-embed-warmup"
        assert release.wait(timeout=10), "Test did not release background initializer"

    monkeypatch.setattr(_memory_connection, "get_embedder", controlled_initializer)
    try:
        for _ in range(2):
            result = tools["trw_recall"].fn(query="Retryworker", max_results=3)
            assert captured["learning_id"] in {entry["id"] for entry in result["learnings"]}
            assert "retrieval_warning" in result
            assert not release.is_set()
        assert entered.wait(timeout=2)
        assert calls == ["trw-embed-warmup"]
    finally:
        release.set()
        worker = _memory_connection._WARMUP_THREAD
        if worker is not None:
            worker.join(timeout=5)
            assert not worker.is_alive()


@pytest.mark.parametrize("ultra_compact", [False, True])
def test_small_entry_budget_preserves_readiness_warning(monkeypatch: pytest.MonkeyPatch, ultra_compact: bool) -> None:
    """The existing minimum-one entry budget is not a whole-response ceiling."""
    tools = _get_tools()
    cfg = get_config()
    monkeypatch.setattr(cfg, "embeddings_enabled", False)
    monkeypatch.setattr(cfg, "nudge_enabled", False)
    captured = tools["trw_learn"].fn(summary="Retryworker bounded retries", scope="project")
    monkeypatch.setattr(cfg, "embeddings_enabled", True)
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr(_memory_connection, "_schedule_embedder_warmup", lambda: False)
    result = tools["trw_recall"].fn(query="Retryworker", token_budget=1, ultra_compact=ultra_compact)
    assert captured["learning_id"] in {entry["id"] for entry in result["learnings"]}
    assert "semantic coverage" in result["retrieval_warning"]
    if not ultra_compact:
        assert result["tokens_budget"] == 1
        assert result["tokens_used"] > 1  # Existing minimum-one guarantee, not a hard byte/token cap.
