"""PRD-DIST-254 §FR03 follow-up: MCP hybrid recall parity with MemoryClient.

`state/_memory_queries._search_entries` (the path the live ``trw_recall`` MCP
tool takes via ``recall_learnings``) historically:

1. Ranked only a ~75-record candidate slice: the ≤25 LIKE-substring keyword
   hits + ``hybrid_vector_candidates`` vector hits.
2. Fused a LIKE-substring keyword ranking (near-noise on a natural-language
   query) against the vector ranking with pure-position RRF.

On the 226-record operator gold set this drove embeddings-ON Recall@5 to 0.583
(vs MemoryClient 0.9375): a gold record sitting at vector rank 0 was demoted to
fused rank 5-7 because ~10 irrelevant LIKE hits leapfrogged it under RRF.

The fix routes the hybrid branch through the SAME
``trw_memory.retrieval.pipeline.hybrid_search`` (BM25 + dense + RRF) the
MemoryClient path uses, over the full candidate pool. These tests drive the
REAL fusion (no mocked fuser) with REAL stored embeddings so the dense ranker
runs, and assert the fused ORDER, not mere existence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from trw_memory.models.memory import MemoryEntry

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_adapter import _search_entries, get_backend

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


class _VecEmbedder:
    """Embedder returning a fixed query vector.

    Combined with a patched ``get_stored_embeddings`` that maps each entry id to
    a controlled vector, this drives the real cosine ``dense_search`` ranker
    deterministically while exercising the real BM25 + RRF fusion.
    """

    def __init__(self, query_vec: list[float]) -> None:
        self._q = query_vec

    def embed(self, _text: str) -> list[float]:
        return self._q

    def available(self) -> bool:
        return True


def _store(backend: Any, eid: str, content: str, *, importance: float = 0.5) -> MemoryEntry:
    entry = MemoryEntry(id=eid, content=content, detail="d", importance=importance)
    backend.store(entry)
    return entry


def _run(
    backend: Any,
    query: str,
    *,
    query_vec: list[float],
    stored: dict[str, list[float]],
    top_k: int = 10,
    namespace: str | None = "default",
    min_impact: float = 0.0,
) -> list[str]:
    cfg = TRWConfig()
    with (
        patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=_VecEmbedder(query_vec),
        ),
        patch.object(backend, "get_stored_embeddings", return_value=stored),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        results = _search_entries(backend, query, top_k=top_k, namespace=namespace, min_impact=min_impact)
    return [e.id for e in results]


class TestHybridParity:
    def test_strong_vector_hit_not_demoted_by_noisy_keyword_matches(self, trw_dir: Path) -> None:
        """A doc the dense ranker puts FIRST must not be buried by LIKE matches.

        The gold doc carries the discriminating query tokens ("corrupted",
        "writers") AND is dense-nearest. A pile of distractor docs share only the
        high-frequency filler token "the" with the query (3x each, high TF).
        Under the old pure-position LIKE+vector RRF the filler-token distractors
        leapfrogged the gold doc; BM25 down-weights "the" (low IDF) so the gold
        doc's discriminating-token match plus its dense rank keep it on top.
        """
        backend = get_backend(trw_dir)
        _store(backend, "L-gold", "database corrupted with concurrent writers")
        for i in range(8):
            _store(backend, f"L-noise-{i}", f"the the the unrelated topic {i}")

        query = "the database got corrupted with concurrent writes"
        query_vec = [1.0, 0.0, 0.0]
        stored = {"L-gold": [1.0, 0.0, 0.0]}  # identical → cosine 1.0, dense rank 1
        for i in range(8):
            stored[f"L-noise-{i}"] = [0.0, 1.0, 0.0]  # orthogonal → cosine 0

        ids = _run(backend, query, query_vec=query_vec, stored=stored)

        assert ids, "hybrid recall returned nothing"
        assert ids[0] == "L-gold", f"strong vector hit was demoted by noisy keyword matches: {ids[:5]}"

    def test_pool_widening_surfaces_record_beyond_like_slice(self, trw_dir: Path) -> None:
        """A vector-only gold record must surface even with many LIKE distractors.

        Seed 40 lexically-unrelated decoys plus one gold record that does NOT
        lexically match the query but IS dense-nearest. The decoys are dense-far.
        The old path capped the keyword slice + vector candidates at ~75 entries,
        so a vector-only gold could be lost entirely on a larger namespace. The
        widened pool + dense ranking surfaces it at the top.
        """
        backend = get_backend(trw_dir)
        _store(backend, "L-vec-gold", "ed25519 signing key rotation runbook", importance=0.9)
        for i in range(40):
            _store(backend, f"L-decoy-{i}", f"unrelated filesystem topic number {i}")

        # Query shares no content tokens with any doc → BM25 contributes nothing
        # to anyone, isolating the dense ranker + pool-widening effect.
        query = "rotate the signing credential"
        query_vec = [1.0, 0.0, 0.0]
        stored = {"L-vec-gold": [1.0, 0.0, 0.0]}
        for i in range(40):
            stored[f"L-decoy-{i}"] = [0.0, 1.0, 0.0]

        ids = _run(backend, query, query_vec=query_vec, stored=stored, top_k=5)

        assert "L-vec-gold" in ids, f"vector-only gold record missing from top-5: {ids}"
        assert ids[0] == "L-vec-gold"

    def test_no_embedder_falls_back_to_keyword(self, trw_dir: Path) -> None:
        """When no embedder is available the path degrades to keyword-only.

        Graceful degradation: the hybrid pool/BM25 path must NEVER fail when
        embeddings are off — it returns the existing LIKE keyword results.
        """
        backend = get_backend(trw_dir)
        _store(backend, "L-kw", "unique distinctive marker token zebra")
        _store(backend, "L-other", "completely different content")

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=None,
            ),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
        ):
            results = _search_entries(backend, "zebra")

        ids = [e.id for e in results]
        assert "L-kw" in ids, "keyword fallback lost the matching record"

    def test_raising_embed_local_only_violation_falls_back_to_keyword(self, trw_dir: Path) -> None:
        """A raising ``embedder.embed`` (trw_memory MemoryError family) degrades.

        Hardening (verifier note, 2026-06-10): the embedder is PRESENT (so the
        early ``embedder is None`` guard is bypassed) but ``embed()`` raises a
        ``trw_memory.exceptions.LocalOnlyViolationError`` — a ``MemoryError``
        subclass that the original ``(OSError, ValueError, RuntimeError,
        ImportError)`` except tuple did NOT catch, so the exception would have
        escaped and crashed the recall instead of degrading to keyword. This must
        return the LIKE keyword results, not raise.
        """
        from trw_memory.exceptions import LocalOnlyViolationError

        backend = get_backend(trw_dir)
        _store(backend, "L-kw", "unique distinctive marker token zebra")
        _store(backend, "L-other", "completely different content")

        class _RaisingEmbedder:
            def embed(self, _text: str) -> list[float]:
                raise LocalOnlyViolationError("network blocked by local-only mode")

            def available(self) -> bool:
                return True

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=_RaisingEmbedder(),
            ),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
        ):
            results = _search_entries(backend, "zebra")

        ids = [e.id for e in results]
        assert "L-kw" in ids, "raising embed must degrade to keyword, not crash"

    def test_raising_embed_type_error_falls_back_to_keyword(self, trw_dir: Path) -> None:
        """A ``TypeError`` from the embed/hybrid path also degrades to keyword.

        Hardening (verifier note, 2026-06-10): a misconfigured embedder returning
        a non-vector (or an upstream signature mismatch) surfaces as ``TypeError``
        inside ``hybrid_search`` — also not in the original except tuple. Must
        fall back rather than escape.
        """
        backend = get_backend(trw_dir)
        _store(backend, "L-kw", "unique distinctive marker token zebra")

        class _BadVecEmbedder:
            def embed(self, _text: str) -> list[float]:
                raise TypeError("embed got an unexpected vector shape")

            def available(self) -> bool:
                return True

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=_BadVecEmbedder(),
            ),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
        ):
            results = _search_entries(backend, "zebra")

        ids = [e.id for e in results]
        assert "L-kw" in ids, "TypeError must degrade to keyword, not crash"

    def test_namespace_none_searches_all_tiers(self, trw_dir: Path) -> None:
        """``namespace=None`` (user-tier federation) must still hybrid-rank.

        The user store holds only ``user:<id>`` entries; recall passes
        ``namespace=None`` to search across them. The widened-pool candidate
        scan must honour that or federation silently returns nothing.
        """
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(
                id="L-user",
                content="machine local user note about vim keybindings",
                detail="d",
                importance=0.6,
                namespace="user:local",
            )
        )

        ids = _run(
            backend,
            "vim keybindings",
            query_vec=[1.0, 0.0, 0.0],
            stored={"L-user": [1.0, 0.0, 0.0]},
            namespace=None,
        )

        assert "L-user" in ids, "namespace=None hybrid scan dropped the user-tier entry"

    def test_min_impact_filter_applied_to_pool(self, trw_dir: Path) -> None:
        """A low-impact candidate below ``min_impact`` must be excluded.

        Even though the low-impact doc is dense-nearest, the ``min_impact`` floor
        applied at the candidate-pool scan must drop it before ranking.
        """
        backend = get_backend(trw_dir)
        _store(backend, "L-hi", "alpha beta gamma high impact", importance=0.9)
        _store(backend, "L-lo", "alpha beta gamma low impact", importance=0.1)

        ids = _run(
            backend,
            "alpha beta gamma",
            query_vec=[1.0, 0.0, 0.0],
            stored={"L-lo": [1.0, 0.0, 0.0], "L-hi": [0.0, 1.0, 0.0]},
            min_impact=0.5,
        )

        assert "L-lo" not in ids, "min_impact filter did not exclude low-impact candidate"
        assert "L-hi" in ids


# ---------------------------------------------------------------------------
# P1/Item5 — Hybrid pool-cap boundary (known limitation pin test).
# ---------------------------------------------------------------------------


class TestHybridPoolCapBoundary:
    """Pin the hybrid_search_candidate_pool_size=1000 cap behaviour.

    When the namespace holds >1000 entries, ``list_entries`` is called with
    ``limit=1000`` so only the 1000 most-recent (DB insertion order, as
    ``list_entries`` returns newest-first by updated_at) are fetched into the
    candidate pool. Entries inserted earlier may be excluded from ranking.
    This is a DOCUMENTED LIMITATION, not a bug — the cap prevents unbounded
    memory usage for very large stores. This test pins the contract so a
    future change that widens or removes the cap is visible in the diff.
    """

    def test_pool_capped_at_1000_list_entries_limit(self, trw_dir: Path) -> None:
        """With 1050 stored entries, list_entries is called with limit=1000.

        We verify the cap by intercepting list_entries and asserting the
        ``limit`` keyword argument equals exactly 1000 (the default config
        value). The test does NOT require 1050 real DB writes — it patches
        list_entries to avoid the cost while still exercising the cap logic.
        """
        backend = get_backend(trw_dir)
        # Seed enough entries to trigger the hybrid path (non-empty backend).
        _store(backend, "L-seed", "seed entry for hybrid path trigger", importance=0.5)

        captured_limit: list[int] = []
        real_list = backend.list_entries

        def _capturing_list_entries(**kwargs: object) -> list[object]:
            limit = int(kwargs.get("limit", 0))
            captured_limit.append(limit)
            # Return only the one real entry so the rest of the hybrid path
            # proceeds without error.
            return real_list(**kwargs)

        cfg = TRWConfig(hybrid_search_candidate_pool_size=1000)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=_VecEmbedder([1.0, 0.0, 0.0]),
            ),
            patch.object(backend, "list_entries", side_effect=_capturing_list_entries),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
        ):
            _search_entries(backend, "seed entry", top_k=25)

        assert captured_limit, "list_entries was not called — hybrid path not reached"
        # The cap is max(top_k * 5, pool_size) = max(125, 1000) = 1000.
        assert captured_limit[0] == 1000, (
            f"Expected pool cap of 1000 but list_entries was called with limit={captured_limit[0]}"
        )

    def test_pool_cap_excludes_oldest_when_namespace_exceeds_cap(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 1000-entry cap is documented: oldest entries may be excluded.

        With pool_size=5 (a small test cap) and 6 stored entries where the
        oldest entry "L-oldest" has a strong vector match but falls outside
        the pool, it may NOT appear in results. This pins the behaviour:
        the cap is a known limitation, not a silent correctness bug.

        Note: this test documents the KNOWN LIMITATION, not an absolute
        invariant. If the backend's list_entries ordering changes, the test
        may need adjustment — the important signal is that the cap is applied.
        """
        backend = get_backend(trw_dir)

        # Store 6 entries; "L-oldest" first so it may fall outside a pool of 5.
        _store(backend, "L-oldest", "unique distinctive zebra finder token old", importance=0.9)
        for i in range(5):
            _store(backend, f"L-new{i}", f"newer entry noise {i}", importance=0.3)

        # Use pool_size=5 so "L-oldest" might be excluded by the cap.
        cfg = TRWConfig(hybrid_search_candidate_pool_size=5)
        # top_k * 5 = 25 * 5 = 125 — use a small top_k so max(top_k*5, pool_size) = pool_size.
        # We override pool_size to 5; use top_k=1 so max(1*5, 5) = 5.
        captured: list[int] = []
        real_list = backend.list_entries

        def _spy(**kwargs: object) -> list[object]:
            captured.append(int(kwargs.get("limit", 0)))
            return real_list(**kwargs)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=_VecEmbedder([1.0, 0.0, 0.0]),
            ),
            patch.object(backend, "list_entries", side_effect=_spy),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
        ):
            _search_entries(backend, "zebra finder", top_k=1)

        # The limit sent to list_entries must equal the configured pool cap (5).
        assert captured and captured[0] == 5, f"Pool cap 5 not respected; limit was {captured}"
