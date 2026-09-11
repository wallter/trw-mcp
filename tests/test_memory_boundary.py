"""Integration tests for the trw-mcp → trw-memory boundary.

These tests target the serialization fragility at the adapter layer:
- the store path maps summary→content, impact→importance
- ``_memory_to_learning_dict`` reverses that mapping on read-back
- Any regression in either direction breaks the entire learning store

Each test gets a fresh backend via the ``trw_dir`` fixture (backed by tmp_path).
The autouse ``_reset_memory_backend`` fixture in conftest.py closes the singleton
between tests so no state leaks.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance
from trw_memory.exceptions import StorageError
from trw_memory.models.memory import MemoryEntry

from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state.memory_adapter import (
    backfill_embeddings,
    get_backend,
    recall_learnings,
    store_learning,
    update_learning,
)


def _qualified_vectors(backend, vector: list[float]) -> dict[str, StoredVector]:
    """Deterministic test evidence, bound to real SQLite candidate text."""
    space = EmbeddingSpace("a" * 64, "boundary-fixture-v1", len(vector))
    return {
        entry.id: StoredVector(
            tuple(vector), VectorProvenance.for_vector(space, f"{entry.content} {entry.detail}", vector)
        )
        for entry in backend.list_entries(namespace="default")
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_user_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point user-tier federation at an empty tmp dir.

    ``recall_learnings`` federates the user-tier store (PRD-CORE-185 FR06).
    Without this, the operator's real ``~/.trw/memory`` records leak into
    recall results and fail the exact-count assertions on any dev box with a
    populated user store (9 failures observed 2026-06-10). Mirrors the
    isolation pattern in ``test_recall_federation.py``.
    """
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Minimal .trw directory structure for boundary tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "memory").mkdir()
    # No entries dir — tests that need YAML migration create it explicitly
    return d


# ---------------------------------------------------------------------------
# P1-A: StorageError propagation on store
# ---------------------------------------------------------------------------


class TestStorageErrorPropagation:
    """P1-A: SQLiteBackend errors at the adapter boundary.

    These tests enforce the boundary contract: a non-corruption StorageError from
    the SQLite layer is caught by the adapter and translated into a stable result
    shape — an error dict for ``store_learning()``, an empty list for
    ``recall_learnings()`` — never propagated to tool callers.

    The adapter is the last defence before errors surface to MCP tool callers.
    If StorageError leaked out, tools would return an unhandled exception
    traceback rather than a structured result, breaking the JSON-RPC response
    contract. (Previously xfail regression sentinels; unxfailed once the seam in
    ``memory_adapter.store_learning``/``recall_learnings`` translated the error.)
    """

    def test_store_learning_storage_error_returns_error_dict(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When SQLiteBackend.store() raises StorageError, store_learning() must
        return a dict with an 'error' key rather than propagating the exception."""
        backend = get_backend(trw_dir)
        original_store = backend.store

        def raise_storage_error(entry: MemoryEntry) -> None:
            raise StorageError("simulated disk full")

        monkeypatch.setattr(backend, "store", raise_storage_error)

        try:
            result = store_learning(
                trw_dir,
                "L-p1a001",
                "summary that should fail",
                "detail",
                impact=0.7,
            )
            # If the adapter catches the error it must return an error dict,
            # NOT silently succeed and return "recorded".
            assert isinstance(result, dict)
            # Either an error key is present OR the status indicates failure
            failed = "error" in result or result.get("status") != "recorded"
            assert failed, f"Expected error or non-recorded status when StorageError raised, got: {result}"
        except StorageError:
            # StorageError escaping the adapter is a boundary violation — let it
            # propagate so this test hard-fails if the seam ever regresses.
            raise
        finally:
            monkeypatch.setattr(backend, "store", original_store)

    def test_hybrid_recall_does_not_consult_backend_search_at_all(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Renamed from ``..._storage_error_returns_empty_not_exception``.

        That test raised ``StorageError`` from ``backend.search`` and asserted
        ``isinstance(result, (list, dict))``. Two things were wrong with it: the
        assertion admits every possible outcome, and — measured — ``search`` is
        called **zero** times on the hybrid recall path, which ranks a
        ``list_entries`` candidate pool. The exception it "protected against"
        never fired, so the test proved nothing about the seam it named.

        What is worth pinning is the routing fact itself: with a qualified
        embedder and compatible generation records, keyword ``search`` is not on the path. If that changes, the
        StorageError translation seam DOES become reachable and needs its own
        coverage — this test is the tripwire for that.
        """
        store_learning(trw_dir, "L-p1a002", "setup entry", "detail", impact=0.5)

        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 384
        embedder.embedding_space.return_value = EmbeddingSpace("a" * 64, "boundary-fixture-v1", 384)
        embedder.available.return_value = True
        monkeypatch.setattr("trw_mcp.state._memory_connection.get_embedder", lambda: embedder)

        backend = get_backend(trw_dir)
        records = _qualified_vectors(backend, [0.1] * 384)
        monkeypatch.setattr(backend, "get_vector_records", lambda *args, **kwargs: records)
        search_calls: list[object] = []

        def raise_storage_error(*args: object, **kwargs: object) -> list[MemoryEntry]:
            search_calls.append(args)
            raise StorageError("simulated read failure")

        monkeypatch.setattr(backend, "search", raise_storage_error)

        result = recall_learnings(trw_dir, "setup entry")
        assert search_calls == [], (
            "backend.search is now on the hybrid recall path; its StorageError "
            "translation seam is reachable and needs real coverage"
        )
        assert [entry["id"] for entry in result] == ["L-p1a002"], f"hybrid recall lost the stored entry: {result}"

    def test_recall_learnings_list_entries_error_returns_empty(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wildcard recall exercises list_entries(), not search(). Same protection
        applies: StorageError from list_entries must not escape the adapter."""
        store_learning(trw_dir, "L-p1a003", "wildcard entry", "detail")

        backend = get_backend(trw_dir)

        def raise_storage_error(*args: object, **kwargs: object) -> list[MemoryEntry]:
            raise StorageError("list_entries failure")

        monkeypatch.setattr(backend, "list_entries", raise_storage_error)

        result = recall_learnings(trw_dir, "*")
        # The wildcard path has no alternative source, so the honest degraded
        # answer is an EMPTY list — never a partial one presented as complete,
        # and never a raised StorageError. `isinstance(result, (list, dict))`
        # admitted every one of those outcomes.
        assert result == [], f"expected an empty degraded result, got: {result}"


# ---------------------------------------------------------------------------
# P1-B: Field rename round-trip (THE critical test)
# ---------------------------------------------------------------------------


class TestFieldRenameRoundTrip:
    """P1-B: The bidirectional field mapping is the primary serialization fragility.

    trw-mcp uses:  summary / impact  (learning API)
    trw-memory uses: content / importance  (storage layer)

    store_learning():             summary → content, impact → importance
    _memory_to_learning_dict():   content → summary, importance → impact

    Any regression in either direction silently stores/returns data under the
    wrong key, causing tool callers to see None or KeyError.
    """

    def test_store_and_recall_expose_learning_field_names(self, trw_dir: Path) -> None:
        """After storing via store_learning(), recall_learnings() must return
        dicts with 'summary' and 'impact' keys — NOT 'content' or 'importance'."""
        result = store_learning(
            trw_dir,
            "L-rt001",
            "test summary text",
            "detailed explanation",
            impact=0.8,
        )
        assert result["status"] == "recorded"

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1

        entry = recalled[0]
        # These are the external learning-API field names
        assert "summary" in entry, "Field 'summary' missing — likely stored as 'content'"
        assert "impact" in entry, "Field 'impact' missing — likely stored as 'importance'"
        # These are the internal MemoryEntry field names — must NOT appear at boundary
        assert "content" not in entry, "'content' leaked through to external dict"
        assert "importance" not in entry, "'importance' leaked through to external dict"

    def test_stored_summary_value_round_trips_correctly(self, trw_dir: Path) -> None:
        """The summary string must survive the summary→content→summary journey intact."""
        original_summary = "unique boundary test string xyzzy"
        store_learning(trw_dir, "L-rt002", original_summary, "detail", impact=0.6)

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["summary"] == original_summary

    def test_stored_impact_value_round_trips_correctly(self, trw_dir: Path) -> None:
        """The impact float must survive the impact→importance→impact journey intact."""
        original_impact = 0.85
        store_learning(trw_dir, "L-rt003", "summary", "detail", impact=original_impact)

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["impact"] == pytest.approx(original_impact)

    def test_underlying_sqlite_stores_memory_field_names(self, trw_dir: Path) -> None:
        """The SQLite layer must use 'content' and 'importance' — the MemoryEntry
        field names. This verifies the inbound direction of the mapping."""
        store_learning(trw_dir, "L-rt004", "summary for sqlite test", "detail", impact=0.75)

        backend = get_backend(trw_dir)
        raw_entry: MemoryEntry | None = backend.get("L-rt004", namespace=DEFAULT_NAMESPACE)
        assert raw_entry is not None, "Entry not found in SQLite after store_learning()"

        # MemoryEntry must have content and importance (storage field names)
        assert raw_entry.content == "summary for sqlite test", (
            "SQLite MemoryEntry.content should hold the summary string"
        )
        assert raw_entry.importance == pytest.approx(0.75), "SQLite MemoryEntry.importance should hold the impact float"
        # MemoryEntry should NOT have summary or impact attributes
        assert not hasattr(raw_entry, "summary"), (
            "MemoryEntry should not have 'summary' attribute — field name is 'content'"
        )
        assert not hasattr(raw_entry, "impact"), (
            "MemoryEntry should not have 'impact' attribute — field name is 'importance'"
        )

    def test_compact_mode_also_uses_learning_field_names(self, trw_dir: Path) -> None:
        """Compact recall output must also use 'summary' and 'impact', not the
        internal MemoryEntry field names. Compact path has its own dict construction."""
        store_learning(trw_dir, "L-rt005", "compact test summary", "detail", impact=0.5)

        recalled = recall_learnings(trw_dir, "*", compact=True)
        assert len(recalled) == 1
        entry = recalled[0]
        assert "summary" in entry
        assert "impact" in entry
        assert "content" not in entry
        assert "importance" not in entry
        # Compact mode omits detail
        assert "detail" not in entry

    def test_update_learning_maps_summary_to_content_field(self, trw_dir: Path) -> None:
        """update_learning() with summary= must write to MemoryEntry.content, not a
        'summary' column (which doesn't exist in SQLite). This is the update path
        of the field rename — also a regression vector."""
        store_learning(trw_dir, "L-rt006", "original summary", "detail")
        update_learning(trw_dir, "L-rt006", summary="updated summary text")

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["summary"] == "updated summary text"

    def test_update_learning_maps_impact_to_importance_field(self, trw_dir: Path) -> None:
        """update_learning() with impact= must write to MemoryEntry.importance."""
        store_learning(trw_dir, "L-rt007", "summary", "detail", impact=0.3)
        update_learning(trw_dir, "L-rt007", impact=0.95)

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["impact"] == pytest.approx(0.95)

    def test_multiple_entries_all_use_correct_field_names(self, trw_dir: Path) -> None:
        """Field rename correctness must hold for all entries, not just the first.
        Tests that _memory_to_learning_dict is applied consistently in the list path."""
        for i in range(5):
            store_learning(
                trw_dir,
                f"L-rt{100 + i:03d}",
                f"summary number {i}",
                "detail",
                impact=0.1 * (i + 1),
            )

        all_entries = recall_learnings(trw_dir, "*")
        assert len(all_entries) == 5

        for entry in all_entries:
            assert "summary" in entry, f"entry {entry.get('id')} missing 'summary'"
            assert "impact" in entry, f"entry {entry.get('id')} missing 'impact'"
            assert "content" not in entry
            assert "importance" not in entry


# ---------------------------------------------------------------------------
# P2-C: Hybrid search path with embedder
# ---------------------------------------------------------------------------


class TestHybridSearchPath:
    """P2-C: With a qualified provider and generation records, recall_learnings() must
    exercise the hybrid (keyword + vector RRF) path rather than the keyword-only
    fallback. Tests that the embedder wiring in _search_entries is functional."""

    def test_hybrid_path_called_when_embedder_available(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With qualified encoder/vector evidence, recall must exercise the hybrid pipeline.

        PRD-DIST-254 §FR03 follow-up: ``_search_entries`` now delegates to
        ``trw_memory.retrieval.pipeline.hybrid_search`` (BM25 + dense + RRF) — the
        same fusion the MemoryClient path uses — instead of a hand-rolled
        LIKE+vector RRF. We spy on the ``hybrid_search`` the queries module
        imports locally on each call, confirming the hybrid branch was taken.
        """
        store_learning(trw_dir, "L-hyb001", "hybrid search test entry", "detail")

        # 384 dims matches default retrieval_embedding_dim in TRWConfig
        fixed_vector = [0.1] * 384
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = fixed_vector
        mock_embedder.embedding_space.return_value = EmbeddingSpace("a" * 64, "boundary-fixture-v1", 384)
        backend = get_backend(trw_dir)
        records = _qualified_vectors(backend, fixed_vector)
        monkeypatch.setattr(backend, "get_vector_records", lambda *args, **kwargs: records)
        mock_embedder.available.return_value = True

        # Patch get_embedder at its definition site (local import in
        # _search_entries resolves via sys.modules[...]._memory_connection).
        monkeypatch.setattr(
            "trw_mcp.state._memory_connection.get_embedder",
            lambda: mock_embedder,
        )

        # Spy on hybrid_search at its definition site. _search_entries does a
        # local 'from trw_memory.retrieval.pipeline import hybrid_search' on each
        # call, so patching the attribute on the already-loaded pipeline module
        # is the correct interception point.
        try:
            from trw_memory.retrieval import pipeline as pipeline_mod
        except ImportError:
            pytest.skip("trw_memory.retrieval.pipeline not available")

        hybrid_called = False
        original_hybrid = pipeline_mod.hybrid_search

        def spy_hybrid(*args: object, **kwargs: object) -> list:
            nonlocal hybrid_called
            hybrid_called = True
            return original_hybrid(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(pipeline_mod, "hybrid_search", spy_hybrid)

        results = recall_learnings(trw_dir, "hybrid search")
        assert isinstance(results, list)
        # The key assertion: hybrid_search was called, meaning we went through the
        # hybrid (BM25 + dense + RRF) branch rather than the keyword fallback.
        assert hybrid_called, (
            "hybrid_search was not called — hybrid branch was not exercised. "
            "Check _search_entries embedder wiring in _memory_queries.py."
        )

    def test_keyword_fallback_when_embedder_unavailable(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When embedder returns None, recall must fall back to keyword search
        without raising any exception.

        Patches get_embedder at its definition site so the local import inside
        _search_entries picks up the patched value.
        """
        store_learning(trw_dir, "L-hyb002", "fallback keyword test", "detail")

        # Patch definition site — the local import in _search_entries reads from here
        monkeypatch.setattr(
            "trw_mcp.state._memory_connection.get_embedder",
            lambda: None,
        )

        results = recall_learnings(trw_dir, "fallback")
        # Keyword fallback must still find the entry
        assert isinstance(results, list)
        assert any(r["id"] == "L-hyb002" for r in results)

    def test_vector_search_exception_falls_back_to_keyword(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the embedder raises during embed(), _search_entries must catch the
        exception and return keyword results instead of propagating.

        The except clause in _search_entries handles (OSError, ValueError, RuntimeError).
        """
        store_learning(trw_dir, "L-hyb003", "exception fallback test", "detail")

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("model load failed")
        mock_embedder.embedding_space.return_value = EmbeddingSpace("a" * 64, "boundary-fixture-v1", 384)
        mock_embedder.available.return_value = True

        # Patch definition site
        monkeypatch.setattr(
            "trw_mcp.state._memory_connection.get_embedder",
            lambda: mock_embedder,
        )

        try:
            results = recall_learnings(trw_dir, "exception fallback")
            assert [entry["id"] for entry in results] == ["L-hyb003"]
            mock_embedder.embed.assert_called_once_with("exception fallback")
        except RuntimeError:
            pytest.fail(
                "RuntimeError from embedder.embed() propagated through _search_entries. "
                "The hybrid path must catch embedder exceptions and fall back to keyword search."
            )


# ---------------------------------------------------------------------------
# Embedding backfill
# ---------------------------------------------------------------------------


class TestEmbeddingBackfill:
    """backfill_embeddings() must process all entries or report skipped/failed counts."""

    def test_backfill_returns_count_dict(self, trw_dir: Path) -> None:
        """backfill_embeddings() always returns a dict with embedded/skipped/failed keys."""
        for i in range(3):
            store_learning(trw_dir, f"L-bf{i:03d}", f"entry {i}", "detail")

        result = backfill_embeddings(trw_dir)

        assert isinstance(result, dict)
        assert "embedded" in result, "Missing 'embedded' count"
        assert "skipped" in result, "Missing 'skipped' count"
        assert "failed" in result, "Missing 'failed' count"

    def test_backfill_no_embedder_returns_zeros(self, trw_dir: Path) -> None:
        """When no embedder is available (default in tests), backfill returns
        zeros without raising."""
        for i in range(2):
            store_learning(trw_dir, f"L-bf2{i:02d}", f"entry {i}", "detail")

        result = backfill_embeddings(trw_dir)
        # Without embedder all counts should be 0 (no embedding, no skip for this reason)
        assert isinstance(result, dict)
        assert result["embedded"] == 0

    def test_backfill_with_embedder_processes_all_entries(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a mock embedder is available, backfill_embeddings() must call
        embed() for each entry that has content and report embedded==n_entries.

        Uses 384-dim vectors to match the default retrieval_embedding_dim in TRWConfig.
        backfill_embeddings() calls get_embedder() directly (not via a local import),
        so patching the module-level function is sufficient.

        Determinism note: when real ``sentence-transformers`` is installed,
        ``store_learning`` embeds at store time, leaving backfill nothing to do
        (``embedded==0``). To make the invariant hold regardless of environment,
        we force-disable the store-time embedder for the store loop so the entries
        land *unembedded*, then install the mock embedder before calling backfill.
        This preserves the behavioral intent: backfill embeds previously-unembedded
        entries.
        """
        import trw_mcp.state._memory_connection as conn_mod

        # Phase 1: store with NO embedder so entries are persisted unembedded,
        # independent of whether real sentence-transformers is installed.
        monkeypatch.setattr(conn_mod, "_embedder", None)
        monkeypatch.setattr(conn_mod, "_embedder_checked", True)

        n_entries = 4
        for i in range(n_entries):
            store_learning(trw_dir, f"L-bf3{i:02d}", f"entry with content {i}", "detail")

        # Phase 2: install the mock embedder so backfill has work to do.
        # 384 dims must match the backend's dim (set at SQLiteBackend construction time
        # from cfg.retrieval_embedding_dim which defaults to 384)
        fixed_vector = [0.5] * 384
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = fixed_vector
        mock_embedder.embedding_space.return_value = EmbeddingSpace("a" * 64, "boundary-fixture-v1", 384)
        backend = get_backend(trw_dir)
        records = _qualified_vectors(backend, fixed_vector)
        monkeypatch.setattr(backend, "get_vector_records", lambda *args, **kwargs: records)
        mock_embedder.available.return_value = True

        monkeypatch.setattr(conn_mod, "_embedder", mock_embedder)
        monkeypatch.setattr(conn_mod, "_embedder_checked", True)

        result = backfill_embeddings(trw_dir)
        assert result["embedded"] == n_entries, (
            f"Expected {n_entries} entries embedded, got {result['embedded']}. Full result: {result}"
        )
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# Concurrent singleton access
# ---------------------------------------------------------------------------


class TestConcurrentSingletonAccess:
    """Two threads calling get_backend() simultaneously must receive the SAME
    singleton instance (not two separate databases pointing at the same file,
    which would cause locking and data corruption)."""

    def test_two_threads_get_same_backend_instance(self, trw_dir: Path) -> None:
        """get_backend() is thread-safe: concurrent callers share one SQLiteBackend."""
        backends: list[object] = []
        errors: list[Exception] = []

        def fetch_backend() -> None:
            try:
                b = get_backend(trw_dir)
                backends.append(b)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=fetch_backend)
        t2 = threading.Thread(target=fetch_backend)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Threads raised exceptions: {errors}"
        assert len(backends) == 2, "Expected both threads to receive a backend"
        # Critical assertion: both threads got THE SAME object
        assert backends[0] is backends[1], (
            "Threads received different backend instances — singleton is not thread-safe. "
            f"Got: {backends[0]!r} and {backends[1]!r}"
        )

    def test_singleton_identity_preserved_across_repeated_calls(self, trw_dir: Path) -> None:
        """Sequential calls within a single thread must also return the same instance."""
        b1 = get_backend(trw_dir)
        b2 = get_backend(trw_dir)
        b3 = get_backend(trw_dir)
        assert b1 is b2 is b3, "get_backend() returned different instances on repeated calls"


# ---------------------------------------------------------------------------
# MemoryStatus round-trip through adapter
# ---------------------------------------------------------------------------


class TestMemoryStatusRoundTrip:
    """Status values must survive store → recall → update → recall without
    corruption or silent coercion. The adapter converts MemoryStatus enum
    values to/from the string representation expected by tool callers."""

    def test_default_status_is_active(self, trw_dir: Path) -> None:
        """Freshly stored entries must have status='active' when recalled."""
        store_learning(trw_dir, "L-st001", "active entry", "detail")

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["status"] == "active"

    def test_status_active_stored_and_recalled(self, trw_dir: Path) -> None:
        """Explicitly stored status='active' survives the round-trip."""
        store_learning(trw_dir, "L-st002", "explicit active", "detail")
        recalled = recall_learnings(trw_dir, "*", status="active")
        assert any(r["id"] == "L-st002" for r in recalled)
        entry = next(r for r in recalled if r["id"] == "L-st002")
        assert entry["status"] == "active"

    def test_status_resolved_after_update(self, trw_dir: Path) -> None:
        """After update_learning(status='resolved'), recall must return status='resolved'."""
        store_learning(trw_dir, "L-st003", "will be resolved", "detail")
        update_result = update_learning(trw_dir, "L-st003", status="resolved")
        assert update_result["status"] == "updated"

        # Wildcard recall must return the entry with updated status
        recalled = recall_learnings(trw_dir, "*")
        entry = next((r for r in recalled if r["id"] == "L-st003"), None)
        assert entry is not None, "Entry not found after status update"
        assert entry["status"] == "resolved"

    def test_status_filter_excludes_resolved_from_active_query(self, trw_dir: Path) -> None:
        """Filtering by status='active' must exclude entries with status='resolved'."""
        store_learning(trw_dir, "L-st004", "active one", "detail")
        store_learning(trw_dir, "L-st005", "resolved one", "detail")
        update_learning(trw_dir, "L-st005", status="resolved")

        active_entries = recall_learnings(trw_dir, "*", status="active")
        ids = [str(r["id"]) for r in active_entries]
        assert "L-st004" in ids
        assert "L-st005" not in ids, "Resolved entry appeared in active-only query"

    def test_status_obsolete_round_trip(self, trw_dir: Path) -> None:
        """Status='obsolete' must also survive the round-trip via update path."""
        store_learning(trw_dir, "L-st006", "will be obsolete", "detail")
        update_learning(trw_dir, "L-st006", status="obsolete")

        recalled = recall_learnings(trw_dir, "*")
        entry = next((r for r in recalled if r["id"] == "L-st006"), None)
        assert entry is not None
        assert entry["status"] == "obsolete"

    def test_status_string_value_not_enum_object_in_result(self, trw_dir: Path) -> None:
        """_memory_to_learning_dict must return the string value of MemoryStatus,
        not the MemoryStatus enum object itself. Tool callers expect plain strings."""
        store_learning(trw_dir, "L-st007", "string status check", "detail")

        recalled = recall_learnings(trw_dir, "*")
        assert len(recalled) == 1
        status_value = recalled[0]["status"]
        assert isinstance(status_value, str), (
            f"Expected str status, got {type(status_value)}: {status_value!r}. "
            "Check _memory_to_learning_dict enum → string conversion."
        )
        assert status_value in {"active", "resolved", "obsolete"}, f"Unexpected status string value: {status_value!r}"

    def test_status_filter_on_keyword_search_path(self, trw_dir: Path) -> None:
        """Status filter must apply on the keyword search path, not just wildcard.
        Ensures the filter is wired through _search_entries, not just recall_learnings."""
        store_learning(trw_dir, "L-st008", "python active test", "detail")
        store_learning(trw_dir, "L-st009", "python resolved test", "detail")
        update_learning(trw_dir, "L-st009", status="resolved")

        results = recall_learnings(trw_dir, "python", status="active")
        ids = [str(r["id"]) for r in results]
        assert "L-st008" in ids
        assert "L-st009" not in ids, "Resolved entry appeared in active-filtered keyword search"


# ---------------------------------------------------------------------------
# PRD-CORE-251 FR09: the memory-concern boundary ratchet
# ---------------------------------------------------------------------------
#
# ``scripts/check_memory_boundary.py`` is the gate. Phase 1 lands it ARMED WITH
# NOTHING on purpose: no concern has been delegated yet, so the
# re-implementation scan has an empty registry and would pass a tree full of
# duplication. A gate in that state is the kind that rots — green through five
# phases while never once biting. So these tests do two different jobs:
#
# * the checks that bite TODAY (import direction stays guarded, the trw-mcp-only
#   allowlist is present and rationalised, the delegation floor rises with the
#   first ``trw_memory.tools`` import) are asserted against the REAL tree;
# * the check that is armed with nothing is asserted against a PLANTED tree with
#   an injected concern registry, so the mechanism is proven before Phase 2
#   depends on it.

REPO_ROOT = Path(__file__).resolve().parents[2]
_BOUNDARY_SCRIPT = REPO_ROOT / "scripts" / "check_memory_boundary.py"

# Monorepo-only invariant: the repo-root scripts/ layout is absent from the
# standalone trw-mcp mirror, where these gate tests do not apply. The adapter
# tests above are NOT monorepo-only, so the skip is per-test, not module-level.
monorepo_only = pytest.mark.skipif(
    not _BOUNDARY_SCRIPT.is_file(),
    reason="monorepo-only invariant (repo-root scripts/ absent in mirror)",
)


def _load_gate() -> object:
    """Load the gate script as a module (it is a script, not an installed package)."""
    spec = importlib.util.spec_from_file_location("check_memory_boundary", _BOUNDARY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass declarations can resolve
    # annotations — dataclasses looks the defining module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@monorepo_only
def test_the_gate_passes_on_the_current_tree() -> None:
    """The ratchet must be green at the Phase 1 boundary (PRD-CORE-251 NFR02)."""
    assert _load_gate().main() == 0


@monorepo_only
def test_import_direction_is_guarded_by_the_real_scanner() -> None:
    """trw-memory importing trw_mcp must remain a build failure.

    The scan lives in ``scripts/check_import_boundaries.py`` and runs in
    ``make check`` through ``seam-check``; re-implementing it here would be the
    same duplication PRD-CORE-251 exists to delete. What was unguarded is the
    registry entry that makes it apply to trw-memory — this asserts it.
    """
    gate = _load_gate()
    assert gate.check_import_direction_is_guarded() == []
    assert "trw_mcp" in gate._load_import_boundaries().BOUNDARIES["trw-memory"][1]


@monorepo_only
def test_import_direction_check_fails_when_the_boundary_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planted violation: drop trw_mcp from trw-memory's forbidden tuple."""
    gate = _load_gate()
    boundaries_module = gate._load_import_boundaries()
    source_dir, forbidden = boundaries_module.BOUNDARIES["trw-memory"]
    weakened = dict(boundaries_module.BOUNDARIES)
    weakened["trw-memory"] = (source_dir, tuple(root for root in forbidden if root != "trw_mcp"))
    monkeypatch.setattr(boundaries_module, "BOUNDARIES", weakened)
    monkeypatch.setattr(gate, "_load_import_boundaries", lambda: boundaries_module)

    violations = gate.check_import_direction_is_guarded()
    assert len(violations) == 1
    assert "no longer forbids 'trw_mcp'" in violations[0].message


@monorepo_only
def test_every_allowlist_entry_carries_a_rationale() -> None:
    """FR09 AC4 — an unexplained allowlist entry is an exemption nobody can review."""
    gate = _load_gate()
    assert gate.TRW_MCP_ONLY_CONCERNS, "the trw-mcp-only allowlist is empty — it would exempt nothing"
    for concern in gate.TRW_MCP_ONLY_CONCERNS:
        assert concern.rationale.strip(), f"{concern.module} has no rationale"
        assert len(concern.rationale.split()) >= 5, f"{concern.module}'s rationale is not a reason: {concern.rationale}"


@monorepo_only
def test_kept_concerns_still_live_in_trw_mcp() -> None:
    """Section 6's keep list is asserted, not described — a swept module fails here."""
    assert _load_gate().check_kept_concerns_present() == []


@monorepo_only
def test_kept_concern_check_fails_when_a_module_is_swept_away(tmp_path: Path) -> None:
    """Planted violation: the audit-recurrence detector has left trw-mcp."""
    gate = _load_gate()
    kept = gate.KeptConcern("state/consolidation/_audit_patterns.py", "TRW audit vocabulary, not a memory concern")
    violations = gate.check_kept_concerns_present(tree=tmp_path, kept=(kept,))
    assert len(violations) == 1
    assert "absent from trw-mcp" in violations[0].message


@monorepo_only
def test_no_memory_concern_is_reimplemented() -> None:
    """FR09 — the headline gate over the real tree.

    No longer vacuous: Phase 2 armed the store concern, so this scan now has
    something to find. The companion tests below still prove the mechanism on a
    planted tree, because a pass here is a pass over the concerns registered so
    far, not over every memory concern.
    """
    assert _load_gate().scan_reimplementations() == []


@monorepo_only
def test_the_ratchet_is_armed_with_the_phases_that_shipped() -> None:
    """A phase that delegates a concern but forgets to register it is visible here.

    Phase 2 (FR03) is the store concern. Each later phase adds its own entry in
    the change that lands it; the assertion is on the phases DELEGATED, so a
    registration that runs ahead of the delegation fails just as loudly as one
    that lags behind it.
    """
    concerns = _load_gate().DELEGATED_CONCERNS
    assert {concern.phase for concern in concerns} == {2}, (
        "DELEGATED_CONCERNS no longer matches the phases that have shipped — register the "
        "concern in the change that delegates it, and confirm its trw-mcp implementation "
        "was actually deleted."
    )
    store = next(concern for concern in concerns if concern.name == "store")
    assert store.owner == "trw_memory.tools.store.memory_store_impl"
    assert "_learning_to_memory_entry" in store.symbols, (
        "the retired hand builder must stay in the ratchet — re-introducing it is exactly "
        "the second-write-path regression FR03 removed"
    )


@monorepo_only
def test_a_planted_reimplementation_fails_the_gate(tmp_path: Path) -> None:
    """The mechanism Phases 2-5 depend on, proven before they depend on it."""
    gate = _load_gate()
    concern = gate.Concern(
        name="dedup",
        owner="trw_memory.lifecycle.dedup",
        symbols=frozenset({"check_duplicate"}),
        phase=3,
    )
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "dedup.py").write_text(
        "def check_duplicate(entry: dict[str, object]) -> None:\n    return None\n",
        encoding="utf-8",
    )

    violations = gate.scan_reimplementations(tree=tmp_path, concerns=(concern,), kept=())
    assert len(violations) == 1
    assert violations[0].where == "trw-mcp/src/trw_mcp/state/dedup.py"
    assert "dedup" in violations[0].message
    assert "trw_memory.lifecycle.dedup" in violations[0].message, "the message must name the trw-memory owner"


@monorepo_only
def test_an_allowlisted_module_may_define_a_delegated_symbol(tmp_path: Path) -> None:
    """FR09/US-005 AC2 — the allowlist is what keeps the gate from blocking kept concerns."""
    gate = _load_gate()
    concern = gate.Concern(
        name="consolidation cycle",
        owner="trw_memory.lifecycle.consolidation",
        symbols=frozenset({"consolidate_cycle"}),
        phase=3,
    )
    (tmp_path / "state" / "consolidation").mkdir(parents=True)
    (tmp_path / "state" / "consolidation" / "_audit_patterns.py").write_text(
        "def consolidate_cycle() -> None:\n    return None\n", encoding="utf-8"
    )
    kept = (gate.KeptConcern("state/consolidation/_audit_patterns.py", "TRW audit vocabulary, not a memory concern"),)

    assert gate.scan_reimplementations(tree=tmp_path, concerns=(concern,), kept=kept) == []


@monorepo_only
def test_the_store_path_is_the_first_tool_surface_importer() -> None:
    """PRD-CORE-251 Phase 2 landed the first delegation; Phase 1 measured zero.

    This is the counter FR09 hands to the floor check, and it is now non-zero,
    which is what ARMS that check. Each later phase adds importers; this asserts
    the store path is among them rather than pinning an exact list.
    """
    importers = _load_gate().count_tool_surface_imports()
    assert "state/memory_adapter.py" in importers, importers


@monorepo_only
def test_the_declared_floor_covers_the_live_delegation() -> None:
    """With a live importer the floor check is armed, and the declared floor clears it.

    Phase 1 could only assert this check was inert. It is now evaluated against
    the real tree and the real pyproject: a floor below the release that first
    ships ``MemoryToolSurface`` would fail HERE rather than at a user's first
    tool call.
    """
    gate = _load_gate()
    assert gate.count_tool_surface_imports(), "no importer — this assertion would be vacuous"
    assert gate.check_delegation_floor() == []


@monorepo_only
def test_the_first_tool_surface_import_forces_the_floor_up(tmp_path: Path) -> None:
    """FR01 — a delegating trw-mcp with a stale floor fails the build.

    This is what makes a version skew fail at INSTALL time rather than at the
    first tool call, once there is a call to fail. Planted rather than live,
    because trw-mcp does not import the surface yet.
    """
    gate = _load_gate()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "memory_adapter.py").write_text(
        "from trw_memory.tools import memory_store_impl\n", encoding="utf-8"
    )
    stale = tmp_path / "pyproject.toml"
    stale.write_text('[project]\nname = "trw-mcp"\ndependencies = ["trw-memory>=0.12.0,<1.0.0"]\n', encoding="utf-8")

    assert gate.count_tool_surface_imports(tmp_path) == ["state/memory_adapter.py"]
    violations = gate.check_delegation_floor(tree=tmp_path, pyproject=stale)
    assert len(violations) == 1
    assert gate.PROTOCOL_MIN_VERSION in violations[0].message

    current = tmp_path / "current.toml"
    current.write_text(
        f'[project]\nname = "trw-mcp"\ndependencies = ["trw-memory>={gate.PROTOCOL_MIN_VERSION},<1.0.0"]\n',
        encoding="utf-8",
    )
    assert gate.check_delegation_floor(tree=tmp_path, pyproject=current) == []
