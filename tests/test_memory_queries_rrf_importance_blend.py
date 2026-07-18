"""F15 / R-FUSION-001: in-process recall RRF fusion must blend learning importance.

`state/_memory_queries._search_entries` (the trw-mcp in-process recall path,
exercised by `recall_factories`/`_session_recall_phase`) historically fused the
keyword and vector rankings with PURE-position RRF — it ignored each candidate's
learning importance/impact. The MemoryClient path
(`trw_memory.retrieval.pipeline.hybrid_search`) correctly blends importance via
`rrf_fuse(..., importances=..., alpha=...)`. This drove a parity gap: the same
corpus recalled differently depending on which code path served the query.

These tests drive the REAL `rrf_fuse` (NOT a mock) through `_search_entries` so
the blend math actually runs:

  * A higher-importance candidate sitting at a WORSE lexical/vector position
    ranks UP under blending (alpha < 1.0) — importance now affects recall order.
  * alpha=1.0 reproduces the legacy pure-position ordering bit-for-bit
    (back-compat), with NO importances passed to the fuser.

We assert the actual fused ranking ORDER, not mere existence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from trw_memory.models.memory import MemoryEntry

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_adapter import _search_entries, get_backend

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


def _two_candidate_setup(
    backend: Any,
    *,
    high_impact_id: str,
    high_impact: float,
    low_impact_id: str,
    low_impact: float,
) -> tuple[MemoryEntry, MemoryEntry]:
    """Store two entries; the high-impact one sits at the WORSE rank position.

    Both the BM25 (identical shared text) and the dense ranker put
    ``low_impact_id`` first and ``high_impact_id`` second, so pure-position RRF
    ranks the low-impact entry above the high-impact one. Only importance
    blending can flip that.

    Ordering note: the recall path delegates to ``trw_memory.hybrid_search``,
    which fuses BM25 *before* dense. With identical shared text the BM25 scores
    tie, so BM25 preserves the ``list_entries`` order, which is
    ``updated_at DESC``. ``MemoryEntry.updated_at`` is stamped at CONSTRUCTION
    time (``default_factory=datetime.now``), so the entry constructed LAST is
    the "newest" and would lead the tied BM25 ranking. We therefore construct
    ``high_entry`` FIRST and ``low_entry`` LAST so the low-impact entry is the
    newer one and genuinely leads BOTH rankers under pure position — otherwise
    the symmetric-position tie would break toward the high-impact entry and the
    pure-position path would no longer be a clean low-first baseline.
    """
    high_entry = MemoryEntry(
        id=high_impact_id,
        content="recall fusion candidate token",
        detail="d-high",
        importance=high_impact,
    )
    low_entry = MemoryEntry(
        id=low_impact_id,
        content="recall fusion candidate token",
        detail="d-low",
        importance=low_impact,
    )
    backend.store(high_entry)
    backend.store(low_entry)
    return high_entry, low_entry


class _Embedder:
    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def available(self) -> bool:
        return True


def _run_search(
    backend: Any,
    *,
    alpha: float,
    low_impact_id: str,
    high_impact_id: str,
    high_entry: MemoryEntry,
    low_entry: MemoryEntry,
) -> list[str]:
    """Drive `_search_entries` through the REAL `hybrid_search` + `rrf_fuse`.

    The dense ranker is driven via patched stored embeddings: the low-impact
    entry is the closer vector (cosine 1.0), the high-impact entry is slightly
    farther (cosine ~0.9998), so BOTH the dense ranker and BM25 put the
    low-impact entry first. Only importance blending (alpha < 1.0) can lift the
    high-impact entry above it. Returns the fused result IDs in ranked order.
    """
    cfg = TRWConfig(hybrid_rrf_importance_alpha=alpha)
    # low entry: identical to query vector (rank 1); high entry: marginally
    # farther so it ranks 2 on the dense ranker (tie broken toward low).
    stored = {
        low_impact_id: [1.0, 0.0, 0.0],
        high_impact_id: [0.999, 0.0447, 0.0],
    }

    with (
        patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=_Embedder(),
        ),
        patch.object(backend, "get_stored_embeddings", return_value=stored),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        results = _search_entries(backend, "recall fusion candidate token")
    return [e.id for e in results]


class TestRrfImportanceBlend:
    def test_high_importance_at_worse_position_ranks_up_under_blend(self, trw_dir: Path) -> None:
        """alpha=0.5: a high-impact entry at the WORSE position outranks the
        low-impact entry at the better position — importance now drives order."""
        backend = get_backend(trw_dir)
        high_entry, low_entry = _two_candidate_setup(
            backend,
            high_impact_id="L-rrfblend-hi",
            high_impact=0.95,
            low_impact_id="L-rrfblend-lo",
            low_impact=0.1,
        )

        ids = _run_search(
            backend,
            alpha=0.5,
            low_impact_id="L-rrfblend-lo",
            high_impact_id="L-rrfblend-hi",
            high_entry=high_entry,
            low_entry=low_entry,
        )

        # Both present, but the HIGH-impact entry is now ranked FIRST despite
        # sitting at the worse lexical/vector position.
        assert ids == ["L-rrfblend-hi", "L-rrfblend-lo"]

    def test_alpha_one_reproduces_pure_position(self, trw_dir: Path) -> None:
        """alpha=1.0: legacy pure-position ordering — the low-impact entry at the
        better position stays first; importance does NOT affect order (back-compat)."""
        backend = get_backend(trw_dir)
        high_entry, low_entry = _two_candidate_setup(
            backend,
            high_impact_id="L-rrfpure-hi",
            high_impact=0.95,
            low_impact_id="L-rrfpure-lo",
            low_impact=0.1,
        )

        ids = _run_search(
            backend,
            alpha=1.0,
            low_impact_id="L-rrfpure-lo",
            high_impact_id="L-rrfpure-hi",
            high_entry=high_entry,
            low_entry=low_entry,
        )

        # Pure position: the better-positioned (low-impact) entry stays first.
        assert ids == ["L-rrfpure-lo", "L-rrfpure-hi"]

    def test_blend_flips_relative_to_pure_position(self, trw_dir: Path) -> None:
        """Direct A/B: identical corpus + rankings, only alpha differs — the
        relative order of the two candidates inverts between pure-position and
        blended fusion. Proves the config field is load-bearing on recall order."""
        backend = get_backend(trw_dir)
        high_entry, low_entry = _two_candidate_setup(
            backend,
            high_impact_id="L-rrfab-hi",
            high_impact=0.95,
            low_impact_id="L-rrfab-lo",
            low_impact=0.1,
        )

        pure = _run_search(
            backend,
            alpha=1.0,
            low_impact_id="L-rrfab-lo",
            high_impact_id="L-rrfab-hi",
            high_entry=high_entry,
            low_entry=low_entry,
        )
        blended = _run_search(
            backend,
            alpha=0.5,
            low_impact_id="L-rrfab-lo",
            high_impact_id="L-rrfab-hi",
            high_entry=high_entry,
            low_entry=low_entry,
        )

        assert pure == ["L-rrfab-lo", "L-rrfab-hi"]
        assert blended == ["L-rrfab-hi", "L-rrfab-lo"]
        # The change is purely attributable to importance blending.
        assert pure != blended
