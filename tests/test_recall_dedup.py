"""Unit tests for post-rank near-duplicate dedup (F-DEDUP-001)."""

from __future__ import annotations

import pytest

from trw_mcp.state._store_selection import VectorSet
from trw_mcp.tools._recall_dedup import _content_key, _cosine, dedup_ranked_learnings

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
    deduped, collapsed = dedup_ranked_learnings(entries, vectors_fn=lambda ids: VectorSet(embeddings, 0.9))
    assert [e["id"] for e in deduped] == ["L-0", "L-2"]
    assert collapsed == 1


def test_cosine_pass_below_threshold_keeps_entries() -> None:
    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b")]
    embeddings = {"L-0": [1.0, 0.0], "L-1": [0.5, 0.866]}  # ~60 deg apart, well below 0.9
    deduped, collapsed = dedup_ranked_learnings(entries, vectors_fn=lambda ids: VectorSet(embeddings, 0.9))
    assert collapsed == 0
    assert len(deduped) == 2


def test_vectors_fn_failure_is_fail_open() -> None:
    """A raising vectors_fn must not block recall; exact-deduped entries returned."""

    def boom(_ids: list[str]) -> VectorSet | None:
        raise RuntimeError("backend down")

    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b")]
    deduped, collapsed = dedup_ranked_learnings(entries, vectors_fn=boom)
    assert [e["id"] for e in deduped] == ["L-0", "L-1"]
    assert collapsed == 0


def test_the_daemons_threshold_decides_not_a_local_constant() -> None:
    """PRD-CORE-302 C2: a pair at cosine 0.8 collapses under a 0.75 threshold and survives a 0.9 one."""
    entries = [_entry("L-0", summary="a"), _entry("L-1", summary="b")]
    embeddings = {"L-0": [1.0, 0.0], "L-1": [0.8, 0.6]}

    assert dedup_ranked_learnings(entries, vectors_fn=lambda ids: VectorSet(embeddings, 0.75))[1] == 1
    assert dedup_ranked_learnings(entries, vectors_fn=lambda ids: VectorSet(embeddings, 0.9))[1] == 0


def test_no_embedder_in_the_daemon_collapses_exact_content_only() -> None:
    entries = [_entry("L-0"), _entry("L-1"), _entry("L-2", summary="other")]

    deduped, collapsed = dedup_ranked_learnings(entries, vectors_fn=lambda ids: None)

    assert ([e["id"] for e in deduped], collapsed) == (["L-0", "L-2"], 1)
