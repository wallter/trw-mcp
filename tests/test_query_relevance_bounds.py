"""PRD-CORE-318 FR03: `query_relevance` bounds its query like trw-memory's `bounded_query` (B71-78).

Before the fix the scorer re-split the caller's tokens with no chars/terms cap, so an
oversized query reached `bm25_search` and the dependency-free fallback loop unbounded.
"""

from __future__ import annotations

import time

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace
from trw_memory.retrieval.lexical import MAX_QUERY_CHARS, MAX_QUERY_TERMS

from tests._timing import assert_budget
from trw_mcp.scoring import _query_relevance
from trw_mcp.scoring._query_relevance import query_relevance
from trw_mcp.state._recall_signals import recall_signal_scope


def _matches(n: int = 3) -> list[dict[str, object]]:
    return [
        {"summary": f"entry {i} about auth token refresh", "detail": "session pooling", "tags": ["auth"]}
        for i in range(n)
    ]


def _spy_bm25(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace bm25_search with an empty-result spy so the fallback loop runs too."""
    seen: list[str] = []

    def spy(query: str, entries: list[object], top_k: int) -> list[tuple[str, float]]:
        seen.append(query)
        return []

    monkeypatch.setattr(_query_relevance, "bm25_search", spy)
    return seen


def test_terms_past_the_cap_reach_neither_bm25_nor_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy_bm25(monkeypatch)
    # "auth" matches every entry, but only as the 10,001st term: past the cap.
    scores = query_relevance(_matches(), [f"term{i}" for i in range(10_000)] + ["auth"])

    assert len(seen[0].split()) == MAX_QUERY_TERMS
    assert scores == [0.0, 0.0, 0.0]


def test_characters_past_the_cap_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy_bm25(monkeypatch)
    query_relevance(_matches(), ["x" * (MAX_QUERY_CHARS * 5)])

    assert len(seen[0]) <= MAX_QUERY_CHARS


def test_long_quoted_phrase_degrades_to_terms_like_bounded_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy_bm25(monkeypatch)
    words = [f"w{i}" for i in range(100)]
    query_relevance(_matches(), [f'"{words[0]}', *words[1:-1], f'{words[-1]}"'])

    bounded = seen[0].split()
    assert len(bounded) == MAX_QUERY_TERMS
    assert bounded[0] == '"w0'
    assert not seen[0].endswith('"')


def test_dense_evidence_still_applies_to_an_oversized_query() -> None:
    """The dense leg compares the recall query to the scorer's tokens; both sides are bounded."""
    raw = " ".join(["network", "repair", *(f"pad{i}" for i in range(200))])
    rows: list[dict[str, object]] = [
        {"summary": "unrelated", "detail": "", "tags": []},
        {"summary": "other", "detail": "", "tags": []},
    ]
    space = EmbeddingSpace("a" * 64, "fixture-document-v1", 2)
    with recall_signal_scope(raw) as signals:
        signals.bind_dense(
            rows[1],
            query=raw,
            provider=object(),
            query_vector=[1.0, 0.0],
            store=object(),
            namespace="default",
            entry_id="other",
            cosine=0.9,
            verified_space=space,
        )
        scores = query_relevance(rows, raw.split())

    assert scores[1] > scores[0]


@pytest.mark.parametrize(
    "tokens",
    [["x" * 100] * 50, [f"term{i}" for i in range(500)]],
    ids=["5000-chars", "500-terms"],
)
def test_oversized_query_still_scores_every_match(tokens: list[str]) -> None:
    """Correctness half (unmarked, gating): the bound doesn't drop a match."""
    scores = query_relevance(_matches(), tokens)
    assert len(scores) == 3


@pytest.mark.requires_local_timing
@pytest.mark.parametrize(
    "tokens",
    [["x" * 100] * 50, [f"term{i}" for i in range(500)]],
    ids=["5000-chars", "500-terms"],
)
def test_oversized_query_scores_under_50ms(tokens: list[str]) -> None:
    """Speed half: a host-resource budget, so it lives behind requires_local_timing."""
    start = time.perf_counter()
    query_relevance(_matches(), tokens)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert_budget("oversized_query_relevance", elapsed_ms, 50.0, "ms")


def test_dense_evidence_survives_whitespace_the_split_discards() -> None:
    """Review r1 P2: both sides are split, rejoined and bounded identically.

    A 1,000-space run fills MAX_QUERY_CHARS in the raw query, so bounding it unsplit keeps
    only "auth" while the scorer's own tokens keep "auth token"; the identity check then
    dropped the dense leg.
    """
    raw = "auth" + " " * 1000 + "token"
    rows: list[dict[str, object]] = [
        {"summary": "unrelated", "detail": "", "tags": []},
        {"summary": "other", "detail": "", "tags": []},
    ]
    with recall_signal_scope(raw) as signals:
        signals.bind_dense(
            rows[1],
            query=raw,
            provider=object(),
            query_vector=[1.0, 0.0],
            store=object(),
            namespace="default",
            entry_id="other",
            cosine=0.9,
            verified_space=EmbeddingSpace("a" * 64, "fixture-document-v1", 2),
        )
        scores = query_relevance(rows, raw.split())

    assert scores[1] > scores[0]
