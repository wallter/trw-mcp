"""Unit tests for post-rank near-duplicate dedup (F-DEDUP-001)."""

from __future__ import annotations

import pytest

from trw_mcp.tools._recall_dedup import (
    DEFAULT_COSINE_DUP_THRESHOLD,
    _content_key,
    _cosine,
    dedup_ranked_learnings,
)

pytestmark = pytest.mark.unit


def _entry(entry_id: str, summary: str = "s", content: str = "c", detail: str = "d") -> dict[str, object]:
    return {"id": entry_id, "summary": summary, "content": content, "detail": detail, "impact": 0.5}


def test_exact_content_duplicates_collapse_keeping_first() -> None:
    """Byte-identical content/detail/summary entries collapse to the first (highest-ranked)."""
    entries = [_entry(f"L-{i}") for i in range(5)]
    deduped, collapsed = dedup_ranked_learnings(entries)
    assert [e["id"] for e in deduped] == ["L-0"]
    assert collapsed == 4


def test_distinct_entries_are_preserved() -> None:
    """Entries with differing content are not collapsed."""
    entries = [_entry("L-0", summary="alpha"), _entry("L-1", summary="beta")]
    deduped, collapsed = dedup_ranked_learnings(entries)
    assert [e["id"] for e in deduped] == ["L-0", "L-1"]
    assert collapsed == 0


def test_single_entry_is_noop() -> None:
    deduped, collapsed = dedup_ranked_learnings([_entry("L-0")])
    assert collapsed == 0
    assert len(deduped) == 1


def test_content_key_collides_for_identical_bodies() -> None:
    assert _content_key(_entry("L-0")) == _content_key(_entry("L-1"))


def test_content_key_differs_for_distinct_detail() -> None:
    assert _content_key(_entry("L-0", detail="x")) != _content_key(_entry("L-1", detail="y"))


def test_cosine_identical_vectors_is_one() -> None:
    assert _cosine([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_degenerate_inputs_return_zero() -> None:
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_pass_collapses_near_duplicate_embeddings() -> None:
    """Distinct text but near-parallel embeddings collapse via the cosine pass."""
    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b"), _entry("L-2", summary="c")]
    embeddings = {
        "L-0": [1.0, 0.0, 0.0],
        "L-1": [0.999, 0.001, 0.0],  # near-parallel to L-0 -> duplicate
        "L-2": [0.0, 1.0, 0.0],  # orthogonal -> distinct
    }
    deduped, collapsed = dedup_ranked_learnings(entries, embeddings_fn=lambda ids: embeddings)
    assert [e["id"] for e in deduped] == ["L-0", "L-2"]
    assert collapsed == 1


def test_cosine_pass_below_threshold_keeps_entries() -> None:
    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b")]
    embeddings = {"L-0": [1.0, 0.0], "L-1": [0.5, 0.866]}  # ~60 deg apart, well below 0.9
    deduped, collapsed = dedup_ranked_learnings(entries, embeddings_fn=lambda ids: embeddings)
    assert collapsed == 0
    assert len(deduped) == 2


def test_embeddings_fn_failure_is_fail_open() -> None:
    """A raising embeddings_fn must not block recall; exact-deduped entries returned."""

    def boom(_ids: list[str]) -> dict[str, list[float]]:
        raise RuntimeError("backend down")

    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b")]
    deduped, collapsed = dedup_ranked_learnings(entries, embeddings_fn=boom)
    assert [e["id"] for e in deduped] == ["L-0", "L-1"]
    assert collapsed == 0


def test_default_cosine_threshold_value() -> None:
    assert DEFAULT_COSINE_DUP_THRESHOLD == 0.9
