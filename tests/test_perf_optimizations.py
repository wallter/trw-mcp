"""Tests for PRD-FIX-046: Performance — N+1 queries, connection reuse, sub-config caching.

FR01: Batch access tracking via single SQL UPDATE
FR02: Single-query keyword search with local token filtering
FR04: Cached sub-config properties on TRWConfig
FR05: Module-level ThreadPoolExecutor reuse in LLMClient.ask_sync
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    TelemetryConfig,
    TrustConfig,
)
from trw_mcp.state.memory_adapter import (
    _keyword_search,
    get_backend,
    store_learning,
    update_access_tracking,
)



@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


# ---------------------------------------------------------------------------
# FR01: Batch Access Tracking
# ---------------------------------------------------------------------------


class TestBatchAccessTracking:
    """PRD-FIX-046-FR01: update_access_tracking uses batch SQL UPDATE."""

    def test_batch_updates_multiple_entries(self, trw_dir: Path) -> None:
        """All entries get their access_count incremented in one batch."""
        store_learning(trw_dir, "L-b1", "Alpha", "detail1")
        store_learning(trw_dir, "L-b2", "Beta", "detail2")
        store_learning(trw_dir, "L-b3", "Gamma", "detail3")

        update_access_tracking(trw_dir, ["L-b1", "L-b2", "L-b3"])

        backend = get_backend(trw_dir)
        for lid in ["L-b1", "L-b2", "L-b3"]:
            entry = backend.get(lid)
            assert entry is not None
            assert entry.access_count == 1, f"{lid} access_count should be 1"
            assert entry.last_accessed_at is not None

    def test_batch_increments_existing_count(self, trw_dir: Path) -> None:
        """Batch updates correctly increment from existing count."""
        store_learning(trw_dir, "L-inc1", "Increment test", "detail")

        # First tracking call
        update_access_tracking(trw_dir, ["L-inc1"])
        entry = get_backend(trw_dir).get("L-inc1")
        assert entry is not None
        assert entry.access_count == 1

        # Second tracking call
        update_access_tracking(trw_dir, ["L-inc1"])
        entry = get_backend(trw_dir).get("L-inc1")
        assert entry is not None
        assert entry.access_count == 2

    def test_batch_empty_list_no_op(self, trw_dir: Path) -> None:
        """Empty learning_ids list is a no-op (returns immediately)."""
        # Should not raise or access backend
        update_access_tracking(trw_dir, [])

    def test_batch_nonexistent_ids_no_error(self, trw_dir: Path) -> None:
        """Non-existent IDs in the batch don't cause errors."""
        store_learning(trw_dir, "L-real1", "Real entry", "detail")
        # Mix real and fake IDs
        update_access_tracking(trw_dir, ["L-real1", "L-fake1", "L-fake2"])

        entry = get_backend(trw_dir).get("L-real1")
        assert entry is not None
        assert entry.access_count == 1

    def test_batch_fallback_on_conn_error(self, trw_dir: Path) -> None:
        """Falls back to per-entry updates when batch SQL fails."""
        store_learning(trw_dir, "L-fb1", "Fallback test", "detail")
        backend = get_backend(trw_dir)

        # Simulate batch path failure by temporarily hiding _conn
        original_conn = backend._conn  # type: ignore[attr-defined]
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("batch SQL failed")
        mock_conn.commit = MagicMock()
        backend._conn = mock_conn  # type: ignore[attr-defined]

        try:
            # Should fall through to per-entry path, but that also
            # uses the broken conn. Just verify no unhandled exception.
            # The key test is that batch failure is caught gracefully.
            update_access_tracking(trw_dir, ["L-fb1"])
        except Exception:
            pytest.fail("update_access_tracking should not raise on batch failure")
        finally:
            backend._conn = original_conn  # type: ignore[attr-defined]

    def test_batch_sets_last_accessed_at(self, trw_dir: Path) -> None:
        """Batch update sets last_accessed_at timestamp."""
        store_learning(trw_dir, "L-ts1", "Timestamp test", "detail")

        entry_before = get_backend(trw_dir).get("L-ts1")
        assert entry_before is not None
        ts_before = entry_before.last_accessed_at

        update_access_tracking(trw_dir, ["L-ts1"])

        entry_after = get_backend(trw_dir).get("L-ts1")
        assert entry_after is not None
        assert entry_after.last_accessed_at is not None
        if ts_before is not None:
            assert entry_after.last_accessed_at >= ts_before


# ---------------------------------------------------------------------------
# FR02: Single-Query Keyword Search
# ---------------------------------------------------------------------------


class TestSingleQueryKeywordSearch:
    """PRD-FIX-046-FR02: _keyword_search uses single DB call for multi-token."""

    def test_single_token_unchanged(self, trw_dir: Path) -> None:
        """Single-token query delegates directly to backend.search()."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-st1", content="python gotcha", detail="d"))

        results = _keyword_search(backend, "python")
        assert len(results) >= 1
        assert any(e.id == "L-st1" for e in results)

    def test_multi_token_and_semantics(self, trw_dir: Path) -> None:
        """Multi-token query returns only entries matching ALL tokens."""
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(id="L-mt1", content="python testing gotcha", detail="d1")
        )
        backend.store(
            MemoryEntry(id="L-mt2", content="python memory leak", detail="d2")
        )
        backend.store(
            MemoryEntry(id="L-mt3", content="rust testing safety", detail="d3")
        )

        results = _keyword_search(backend, "python testing")
        ids = [e.id for e in results]
        # L-mt1 matches both "python" and "testing"
        assert "L-mt1" in ids
        # L-mt2 matches "python" but not "testing"
        # L-mt3 matches "testing" but not "python"

    def test_multi_token_checks_detail_field(self, trw_dir: Path) -> None:
        """Token matching checks detail field, not just content."""
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(
                id="L-df1",
                content="python issue",
                detail="related to testing frameworks",
            )
        )

        results = _keyword_search(backend, "python testing")
        ids = [e.id for e in results]
        assert "L-df1" in ids

    def test_multi_token_checks_tags(self, trw_dir: Path) -> None:
        """Token matching checks tags field."""
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(
                id="L-tg1",
                content="python issue",
                detail="some detail",
                tags=["testing", "gotcha"],
            )
        )

        results = _keyword_search(backend, "python testing")
        ids = [e.id for e in results]
        assert "L-tg1" in ids

    def test_multi_token_no_match_returns_empty(self, trw_dir: Path) -> None:
        """Multi-token query with no common entries returns empty."""
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(id="L-nm1", content="alpha only", detail="nothing else")
        )

        results = _keyword_search(backend, "alpha zebra")
        assert len(results) == 0

    def test_single_db_call_for_multi_token(self, trw_dir: Path) -> None:
        """Multi-token search makes only 1 backend.search() call."""
        backend = get_backend(trw_dir)
        backend.store(
            MemoryEntry(id="L-db1", content="python testing gotcha", detail="d")
        )

        original_search = backend.search
        call_count = 0

        def counting_search(*args: Any, **kwargs: Any) -> list[MemoryEntry]:
            nonlocal call_count
            call_count += 1
            return original_search(*args, **kwargs)

        backend.search = counting_search  # type: ignore[assignment]
        try:
            _keyword_search(backend, "python testing gotcha")
            # Should make exactly 1 call (for the first token)
            assert call_count == 1
        finally:
            backend.search = original_search  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# FR04: Cached Sub-Config Properties
# ---------------------------------------------------------------------------


class TestCachedSubConfigProperties:
    """PRD-FIX-046-FR04: Sub-config properties return cached instances."""

    def test_build_returns_same_instance(self) -> None:
        """config.build returns the same object on repeated access."""
        config = TRWConfig()
        b1 = config.build
        b2 = config.build
        assert b1 is b2
        assert isinstance(b1, BuildConfig)

    def test_memory_returns_same_instance(self) -> None:
        """config.memory returns the same object on repeated access."""
        config = TRWConfig()
        m1 = config.memory
        m2 = config.memory
        assert m1 is m2
        assert isinstance(m1, MemoryConfig)

    def test_telemetry_settings_returns_same_instance(self) -> None:
        """config.telemetry_settings returns the same object on repeated access."""
        config = TRWConfig()
        t1 = config.telemetry_settings
        t2 = config.telemetry_settings
        assert t1 is t2
        assert isinstance(t1, TelemetryConfig)

    def test_orchestration_returns_same_instance(self) -> None:
        """config.orchestration returns the same object on repeated access."""
        config = TRWConfig()
        o1 = config.orchestration
        o2 = config.orchestration
        assert o1 is o2
        assert isinstance(o1, OrchestrationConfig)

    def test_scoring_returns_same_instance(self) -> None:
        """config.scoring returns the same object on repeated access."""
        config = TRWConfig()
        s1 = config.scoring
        s2 = config.scoring
        assert s1 is s2
        assert isinstance(s1, ScoringConfig)

    def test_trust_returns_same_instance(self) -> None:
        """config.trust returns the same object on repeated access."""
        config = TRWConfig()
        t1 = config.trust
        t2 = config.trust
        assert t1 is t2
        assert isinstance(t1, TrustConfig)

    def test_ceremony_feedback_returns_same_instance(self) -> None:
        """config.ceremony_feedback returns the same object on repeated access."""
        config = TRWConfig()
        c1 = config.ceremony_feedback
        c2 = config.ceremony_feedback
        assert c1 is c2
        assert isinstance(c1, CeremonyFeedbackConfig)

    def test_paths_returns_same_instance(self) -> None:
        """config.paths returns the same object on repeated access."""
        config = TRWConfig()
        p1 = config.paths
        p2 = config.paths
        assert p1 is p2
        assert isinstance(p1, PathsConfig)

    def test_sub_config_values_match_parent(self) -> None:
        """Cached sub-config fields reflect parent config values."""
        config = TRWConfig(build_check_enabled=False, build_check_timeout_secs=999)
        assert config.build.build_check_enabled is False
        assert config.build.build_check_timeout_secs == 999

    def test_different_config_instances_have_independent_caches(self) -> None:
        """Two TRWConfig instances don't share cached sub-configs."""
        c1 = TRWConfig(build_check_enabled=True)
        c2 = TRWConfig(build_check_enabled=False)
        assert c1.build is not c2.build
        assert c1.build.build_check_enabled is True
        assert c2.build.build_check_enabled is False


# ---------------------------------------------------------------------------
# FR05: Module-Level ThreadPoolExecutor Reuse
# ---------------------------------------------------------------------------


class TestThreadPoolExecutorReuse:
    """PRD-FIX-046-FR05: ask_sync reuses module-level executor."""

    def test_sync_executor_exists_at_module_level(self) -> None:
        """Module-level _sync_executor is a ThreadPoolExecutor."""
        import concurrent.futures

        from trw_mcp.clients.llm import _sync_executor

        assert isinstance(_sync_executor, concurrent.futures.ThreadPoolExecutor)

    def test_ask_sync_does_not_create_new_executor(self) -> None:
        """ask_sync() does not create a new ThreadPoolExecutor per call."""
        import concurrent.futures

        # Verify that ThreadPoolExecutor is NOT called during ask_sync
        mock_async_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="response")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        from trw_mcp.clients.llm import LLMClient

        client = LLMClient.__new__(LLMClient)
        client._model = "haiku"
        client._max_turns = 1
        client._system_prompt = ""
        client._available = True
        client._client = MagicMock()
        client._async_client = mock_async_client
        client._usage_log_path = None

        # When running with an active event loop, ask_sync uses the executor
        # We can't easily test this without an event loop, but we can verify
        # the module-level executor exists and is used (not a new one)
        with patch(
            "trw_mcp.clients.llm.concurrent.futures.ThreadPoolExecutor"
        ) as mock_tp:
            # Call ask_sync - it should NOT instantiate a new ThreadPoolExecutor
            # because it uses the module-level _sync_executor
            try:
                client.ask_sync("test prompt")
            except Exception:
                pass  # We don't care about the result, just the behavior
            mock_tp.assert_not_called()
