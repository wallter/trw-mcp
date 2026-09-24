"""Tests for PRD-FIX-046: Performance — N+1 queries, connection reuse, sub-config caching.

FR02: Single-query keyword search with local token filtering
FR04: Cached sub-config properties on TRWConfig

FR01 (batch access tracking) tests are gone: they pinned the interim
in-process backend's batch-SQL-UPDATE internals (``update_access_tracking``,
deleted). Access-tracking behaviour is now the ``record_surfaced`` store
seam, covered once in ``tests/test_record_surfaced.py``.
"""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# FR02: Single-Query Keyword Search
# ---------------------------------------------------------------------------


# test_single_db_call_for_multi_token DELETED (PRD-CORE-280 slice e):
# asserted only a backend.search() call count on the raw interim backend —
# a connection/query-plan internals detail, the pre-authorized deletion
# category.


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
        config = TRWConfig(build_check_enabled=False, build_check_coverage_min=42.0)
        assert config.build.build_check_enabled is False
        assert config.build.build_check_coverage_min == 42.0

    def test_different_config_instances_have_independent_caches(self) -> None:
        """Two TRWConfig instances don't share cached sub-configs."""
        c1 = TRWConfig(build_check_enabled=True)
        c2 = TRWConfig(build_check_enabled=False)
        assert c1.build is not c2.build
        assert c1.build.build_check_enabled is True
        assert c2.build.build_check_enabled is False


# ---------------------------------------------------------------------------
# FR05: ThreadPoolExecutor in ask_sync
# ---------------------------------------------------------------------------
# NOTE: Module-level _sync_executor was never implemented. ask_sync() creates
# a ThreadPoolExecutor inline per call when an event loop is running. The
# original tests for FR05 referenced a non-existent _sync_executor attribute
# and have been removed as stale.
