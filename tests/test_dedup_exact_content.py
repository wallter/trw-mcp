"""Behavior tests for embedding-independent exact-content dedup (PRD-CORE-042).

Root-cause fix: ``check_duplicate`` short-circuited to "store" on every default
install because ``embeddings_enabled`` defaults to False, so byte-identical
re-learns accumulated as exact duplicates. The exact-content check now runs
BEFORE the embeddings gate and returns "merge" on an exact backend hit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from structlog.testing import capture_logs

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import _check_exact_content_duplicate, check_duplicate
from trw_mcp.state.persistence import FileStateReader


class TestExactContentDedupBypassesEmbeddingsGate:
    """The exact-content path must run even when embeddings are disabled."""

    def test_identical_content_dedups_with_embeddings_disabled(self, tmp_path: Path, reader: FileStateReader) -> None:
        """A byte-identical re-learn is deduped EVEN WITH embeddings disabled.

        This is the root-cause proof: before the fix, embeddings_enabled=False
        returned "store" unconditionally and the dupe was accumulated.
        """
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        # Embeddings explicitly OFF — the default-install failure mode.
        config = TRWConfig(embeddings_enabled=False)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = "L-existing-exact"

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = check_duplicate(
                "exact duplicate summary",
                "exact duplicate detail",
                entries_dir,
                reader,
                config=config,
            )

        # Merge (not skip) so the new entry's tags/evidence/impact still fold in.
        assert result.action == "merge"
        assert result.existing_id == "L-existing-exact"
        assert result.similarity == 1.0
        # The backend lookup was actually consulted with the right content/detail.
        mock_backend.find_active_by_content.assert_called_once_with("exact duplicate summary", "exact duplicate detail")

    def test_no_exact_match_falls_through_to_store_when_embeddings_disabled(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """No exact backend hit + embeddings off → store (embeddings gate honored)."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=False)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = None

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = check_duplicate(
                "novel summary",
                "novel detail",
                entries_dir,
                reader,
                config=config,
            )

        assert result.action == "store"
        assert result.existing_id is None

    def test_exact_match_short_circuits_before_embedding_path(self, tmp_path: Path, reader: FileStateReader) -> None:
        """An exact hit returns merge WITHOUT touching the embedding fast path.

        Even with embeddings enabled, the exact check wins first — embed() is
        never called because the exact merge returns before the gate.
        """
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = "L-exact-hit"

        embed_called: list[str] = []

        def tracking_embed(text: str) -> list[float]:
            embed_called.append(text)
            return [0.0] * 384

        with (
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend),
            patch("trw_mcp.state.dedup.embed", side_effect=tracking_embed),
        ):
            result = check_duplicate("s", "d", entries_dir, reader, config=config)

        assert result.action == "merge"
        assert result.existing_id == "L-exact-hit"
        # Exact path short-circuited before any embedding was computed.
        assert embed_called == []

    def test_backend_unavailable_fails_open_to_store(self, tmp_path: Path, reader: FileStateReader) -> None:
        """If the backend raises, the exact check fails open (store), never blocks."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=False)

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            side_effect=RuntimeError("backend down"),
        ):
            result = check_duplicate("s", "d", entries_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None


class TestExactContentDedupRealBackendContract:
    """F9 cross-version contract: bind the trw-mcp caller to the REAL trw-memory
    ``find_active_by_content`` method surface.

    The mock-backend tests above pass even if ``find_active_by_content`` were
    deleted from trw-memory — a MagicMock auto-creates the attribute. That is the
    exact skew that bit the reporter (F7/F8/F9 were trw-mcp<->trw-memory version
    drift). These tests run against an in-process ``SQLiteBackend`` (embeddings
    disabled) so the next time the real method surface diverges, the contract
    fails loudly instead of falling open silently.
    """

    def test_exact_content_deduped_via_real_backend(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Two byte-identical learnings: the second is deduped via the exact path,
        and ``dedup_exact_content_unavailable`` is NEVER logged (the fail-open
        branch that would mask a lost method) — proven on a REAL backend."""
        from trw_mcp.state.memory_adapter import get_backend, store_learning

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir(parents=True)
        entries_dir = trw_dir / "learnings" / "entries"
        config = TRWConfig(embeddings_enabled=False)

        summary = "byte identical contract summary"
        detail = "byte identical contract detail body"

        # First store creates the canonical row on the REAL backend.
        store_learning(trw_dir, "L-contract-1", summary, detail)

        # Sanity: the real backend exposes the method and returns the stored id.
        backend = get_backend(trw_dir)
        assert backend.find_active_by_content(summary, detail) == "L-contract-1"

        # Now the dedup caller must resolve the SAME real method and merge.
        with capture_logs() as logs:
            result = check_duplicate(summary, detail, entries_dir, reader, config=config)

        assert result.action == "merge"
        assert result.existing_id == "L-contract-1"
        assert result.similarity == 1.0

        # The fail-open branch must NOT have fired — if it had, the caller would
        # have silently swallowed a missing/renamed method and returned None.
        events = {entry.get("event") for entry in logs}
        assert "dedup_exact_content_unavailable" not in events
        # And it DID take the exact path (positive proof the method was consulted).
        assert "dedup_exact_content_match" in events

    def test_check_exact_content_duplicate_binds_real_method(self, tmp_path: Path) -> None:
        """The lower-level helper resolves trw-memory's real find_active_by_content.

        Directly exercises ``_check_exact_content_duplicate`` against a real
        backend so a removed/renamed method surfaces here (no MagicMock to
        auto-create the attribute and hide the skew)."""
        from trw_mcp.state.memory_adapter import store_learning

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir(parents=True)
        entries_dir = trw_dir / "learnings" / "entries"

        summary = "lower level helper summary"
        detail = "lower level helper detail"
        store_learning(trw_dir, "L-helper-1", summary, detail)

        with capture_logs() as logs:
            hit = _check_exact_content_duplicate(summary, detail, entries_dir)

        assert hit == "L-helper-1"
        events = {entry.get("event") for entry in logs}
        assert "dedup_exact_content_unavailable" not in events

    def test_real_backend_no_match_returns_none(self, tmp_path: Path) -> None:
        """A non-matching query on the real backend returns None without the
        fail-open log (distinguishes "no match" from "method unavailable")."""
        from trw_mcp.state.memory_adapter import store_learning

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir(parents=True)
        entries_dir = trw_dir / "learnings" / "entries"

        store_learning(trw_dir, "L-nomatch-1", "stored summary", "stored detail")

        with capture_logs() as logs:
            hit = _check_exact_content_duplicate("totally different", "no overlap here", entries_dir)

        assert hit is None
        events = {entry.get("event") for entry in logs}
        assert "dedup_exact_content_unavailable" not in events
