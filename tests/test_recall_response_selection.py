"""Response formatting preserves the evidence used by actual MCP selection.

Real execute_recall -> memory_adapter -> isolated SQLite. Remote/context and
write hooks are isolated; no adapter acquisition or utility scorer is replaced.
"""

from datetime import datetime, timedelta, timezone

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._tools_learning_shared import set_project_root  # noqa: F401
from trw_mcp.models.config import get_config
from trw_mcp.state import memory_adapter
from trw_mcp.tools import _recall_impl


@pytest.fixture
def store(set_project_root, monkeypatch):
    for key in ("HOME", "TRW_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(key, str(set_project_root))
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    from trw_mcp.state import _memory_connection

    monkeypatch.setattr(_memory_connection, "get_embedder", lambda: None)
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: (rows, None))
    monkeypatch.setattr(_recall_impl, "build_recall_context", lambda *args, **kwargs: None)
    memory_adapter.reset_backend()
    trw_dir = set_project_root / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = memory_adapter.get_backend(trw_dir)
    yield trw_dir, backend
    memory_adapter.reset_backend()


def seed(backend, competitors):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for index in range(competitors):
        backend.store(
            MemoryEntry(
                id=f"L-e{index:04d}",
                content=f"Sourceprobe episodic observation {index}",
                namespace=memory_adapter._NAMESPACE,
                importance=0.95,
                created_at=start,
                updated_at=start + timedelta(seconds=index + 1),
                metadata={"source_kind": "episodic"},
            )
        )
    backend.store(
        MemoryEntry(
            id="L-d1234",
            content="Sourceprobe durable instruction",
            importance=0.2,
            namespace=memory_adapter._NAMESPACE,
            created_at=start,
            updated_at=start,
            metadata={"source_kind": "semantic_memory"},
        )
    )


def recall(trw_dir, query="Sourceprobe", limit=1, compact=False, **kwargs):
    return _recall_impl.execute_recall(
        query,
        trw_dir,
        get_config(),
        max_results=limit,
        compact=compact,
        include_tiers=["project"],
        _search_patterns=lambda *args: [],
        _collect_context=lambda *args: {},
        **kwargs,
    )


def test_direct_id_lookup_remains_available_beyond_prefetch(store):
    trw_dir, backend = store
    seed(backend, _recall_impl.PREFETCH_MULTIPLIER + 3)
    rows = recall(trw_dir, query="L-d1234")["learnings"]
    assert [row["id"] for row in rows] == ["L-d1234"]


@pytest.mark.parametrize("ultra_compact", [False, True])
@pytest.mark.parametrize("evidence", ["detail", "tag11"])
def test_compact_projection_preserves_existing_utility_winner(store, ultra_compact, evidence):
    """Formatting must not discard fields before existing utility selection."""
    trw_dir, backend = store
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for id, detail, importance in (("L-full123", "secondary evidence", 0.8), ("L-compact123", "", 0.9)):
        backend.store(
            MemoryEntry(
                id=id,
                content=f"Sourceprobe {id}",
                detail=detail if evidence == "detail" else "",
                tags=([f"tag{i}" for i in range(10)] + ["secondary"]) if evidence == "tag11" and detail else [],
                importance=importance,
                namespace=memory_adapter._NAMESPACE,
                created_at=start,
                updated_at=start,
            )
        )
    # Three documents avoid the small-corpus Jaccard fallback, whose length
    # normalization legitimately prefers the short row over eleven tags.
    backend.store(
        MemoryEntry(
            id="L-third123",
            content="Sourceprobe unrelated",
            importance=0.9,
            namespace=memory_adapter._NAMESPACE,
            created_at=start,
            updated_at=start,
        )
    )
    before = {
        id: backend.get(id, namespace=memory_adapter._NAMESPACE).model_dump()
        for id in ("L-full123", "L-compact123", "L-third123")
    }
    full = recall(trw_dir, query="Sourceprobe secondary", compact=False)
    compact = recall(trw_dir, query="Sourceprobe secondary", compact=True, ultra_compact=ultra_compact)
    assert full["total_available"] == 3
    if not ultra_compact:
        assert compact["total_available"] == 3
    assert [row["id"] for row in full["learnings"]] == ["L-full123"]
    assert [row["id"] for row in compact["learnings"]] == ["L-full123"]
    if ultra_compact:
        assert compact["count"] == 1
        assert set(compact["learnings"][0]) <= {"id", "summary", "verification_evidence"}
    assert {id: backend.get(id, namespace=memory_adapter._NAMESPACE).model_dump() for id in before} == before

    if evidence == "tag11":
        # A projection that drops the eleventh tag must fail the winner checks.
        backend.update("L-full123", namespace=memory_adapter._NAMESPACE, tags=[f"tag{i}" for i in range(10)])
        assert recall(trw_dir, query="Sourceprobe secondary")["learnings"][0]["id"] != "L-full123"


def test_compact_budget_counts_projected_fields_and_preserves_tag_cap(store):
    from trw_memory.retrieval.token_budget import estimate_serialized_entry_tokens

    from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

    trw_dir, backend = store
    tags = [f"tag{i}" for i in range(COMPACT_TAGS_CAP + 5)]
    backend.store(
        MemoryEntry(
            id="L-budget123",
            content="Sourceprobe budget",
            detail="long evidence " * 150,
            namespace=memory_adapter._NAMESPACE,
            importance=0.9,
            tags=tags,
        )
    )
    result = recall(trw_dir, compact=True, token_budget=150)
    assert [row["id"] for row in result["learnings"]] == ["L-budget123"]
    row = result["learnings"][0]
    assert row["tags"] == tags[:COMPACT_TAGS_CAP]
    assert "detail" not in row
    assert set(row) <= get_config().recall_compact_fields | {"verification_evidence", "verification_status"}
    assert result["tokens_used"] == sum(estimate_serialized_entry_tokens(entry) for entry in result["learnings"])
    assert result["tokens_used"] <= 150
