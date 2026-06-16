"""Integration tests for the trw-mcp → trw-memory boundary.

These tests target the serialization fragility at the adapter layer:
- ``_learning_to_memory_entry`` maps summary→content, impact→importance
- ``_memory_to_learning_dict`` reverses that mapping on read-back
- Any regression in either direction breaks the entire learning store

Each test gets a fresh backend via the ``trw_dir`` fixture (backed by tmp_path).
The autouse ``_reset_memory_backend`` fixture in conftest.py closes the singleton
between tests so no state leaks.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.exceptions import StorageError
from trw_memory.models.memory import MemoryEntry

from trw_mcp.state.memory_adapter import (
    backfill_embeddings,
    get_backend,
    recall_learnings,
    store_learning,
    update_learning,
)

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

    def test_recall_learnings_storage_error_returns_empty_not_exception(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When SQLiteBackend.search() raises StorageError, recall_learnings() must
        return an empty list (or error dict), NOT propagate an uncaught exception."""
        store_learning(trw_dir, "L-p1a002", "setup entry", "detail", impact=0.5)

        backend = get_backend(trw_dir)

        def raise_storage_error(*args: object, **kwargs: object) -> list[MemoryEntry]:
            raise StorageError("simulated read failure")

        monkeypatch.setattr(backend, "search", raise_storage_error)

        result = recall_learnings(trw_dir, "setup entry")
        # Adapter must return a list (possibly empty) or error dict — not raise
        assert isinstance(result, (list, dict)), f"Expected list or dict, got {type(result)}"

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
        assert isinstance(result, (list, dict))


# ---------------------------------------------------------------------------
# P1-B: Field rename round-trip (THE critical test)
# ---------------------------------------------------------------------------


class TestFieldRenameRoundTrip:
    """P1-B: The bidirectional field mapping is the primary serialization fragility.

    trw-mcp uses:  summary / impact  (learning API)
    trw-memory uses: content / importance  (storage layer)

    _learning_to_memory_entry():  summary → content, impact → importance
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
        raw_entry: MemoryEntry | None = backend.get("L-rt004")
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
    """P2-C: When LocalEmbeddingProvider is available, recall_learnings() must
    exercise the hybrid (keyword + vector RRF) path rather than the keyword-only
    fallback. Tests that the embedder wiring in _search_entries is functional."""

    def test_hybrid_path_called_when_embedder_available(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When an embedder is available, recall must exercise the hybrid pipeline.

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
        mock_embedder.available.return_value = True

        # Patch definition site
        monkeypatch.setattr(
            "trw_mcp.state._memory_connection.get_embedder",
            lambda: mock_embedder,
        )

        try:
            results = recall_learnings(trw_dir, "exception fallback")
            assert isinstance(results, list)
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
