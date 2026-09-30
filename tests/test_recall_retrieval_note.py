"""A recall run with embeddings unavailable says its ranking is keyword-only (INC-119 c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import get_config
from trw_mcp.tools._recall_impl import execute_recall


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


def _recall(trw_dir: Path, query: str) -> dict[str, object]:
    return dict(
        execute_recall(
            query=query,
            trw_dir=trw_dir,
            config=get_config(),
            track=False,
            _adapter_recall=lambda *_a, **_k: [],
            _rank_by_utility=lambda items, *a, **k: list(items),
        )
    )


def test_a_query_with_no_embedder_carries_the_degraded_note(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    fake_memory_store.embedder = {"available": False, "model": None, "loaded": False, "reason": "model_not_cached"}
    note = str(_recall(trw_dir, "widget").get("retrieval_note", ""))
    assert "keyword" in note and "model_not_cached" in note


def test_a_working_embedder_adds_no_note(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    assert "retrieval_note" not in _recall(trw_dir, "widget")


@pytest.mark.parametrize("query", ["", "*"])
def test_a_listing_needs_no_ranking_note(trw_dir: Path, fake_memory_store: FakeMemoryStore, query: str) -> None:
    fake_memory_store.embedder = {"available": False, "model": None, "loaded": False, "reason": "model_not_cached"}
    assert "retrieval_note" not in _recall(trw_dir, query)
