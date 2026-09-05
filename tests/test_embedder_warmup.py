"""First-recall download warm-up guard (council Option A+, 2026-06-10).

On a never-cached box the first ``trw_recall`` with cold init allowed would pay
the all-MiniLM-L6-v2 *download* synchronously, risking an MCP-client timeout on
slow networks. The warm-up runs the cold ``get_embedder()`` load on a daemon
thread kicked off at session_start, never blocking the hot path. Recall degrades
to keyword (existing fallback) while the warm-up is incomplete.

This mirrors the existing ``_schedule_post_recovery_backfill`` single-flight
daemon-thread pattern.
"""

from __future__ import annotations

import threading
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_pressure import WriterCensus


def _unpressured_census(*, threshold: int = 8) -> WriterCensus:
    """A measured census with no peer writers (PRD-CORE-257-FR01)."""
    return WriterCensus(
        writer_pids=(),
        writer_count=0,
        peer_writer_count=0,
        threshold=threshold,
        under_pressure=False,
        census_state="measured",
        identity_state="verified",
        heartbeat_state="measured",
    )


class TestScheduleEmbedderWarmup:
    def teardown_method(self) -> None:
        from trw_mcp.state import _memory_connection

        _memory_connection.reset_embedder()
        # Drain any warm-up thread left running.
        t = _memory_connection._WARMUP_THREAD
        if t is not None and t.is_alive():
            t.join(timeout=5)
        _memory_connection._WARMUP_THREAD = None

    def test_warmup_runs_get_embedder_off_thread(self) -> None:
        """The warm-up thread calls get_embedder() (the cold-load path)."""
        from trw_mcp.state import _memory_connection

        _memory_connection._WARMUP_THREAD = None
        called = threading.Event()
        main_thread = threading.current_thread()
        ran_off_main: list[bool] = []

        def fake_get_embedder() -> object:
            ran_off_main.append(threading.current_thread() is not main_thread)
            called.set()
            return object()

        orig = _memory_connection.get_embedder
        _memory_connection.get_embedder = fake_get_embedder  # type: ignore[assignment]
        try:
            started = _memory_connection._schedule_embedder_warmup()
            assert started is True
            assert called.wait(timeout=5), "warm-up thread did not run get_embedder()"
        finally:
            _memory_connection.get_embedder = orig  # type: ignore[assignment]
            t = _memory_connection._WARMUP_THREAD
            if t is not None:
                t.join(timeout=5)

        assert ran_off_main == [True], "warm-up must run on a background thread, not the caller"

    def test_warmup_is_single_flight(self) -> None:
        """A second call while one warm-up is alive is a no-op (returns False)."""
        from trw_mcp.state import _memory_connection

        _memory_connection._WARMUP_THREAD = None
        release = threading.Event()

        def slow_get_embedder() -> object:
            release.wait(timeout=5)
            return object()

        orig = _memory_connection.get_embedder
        _memory_connection.get_embedder = slow_get_embedder  # type: ignore[assignment]
        try:
            first = _memory_connection._schedule_embedder_warmup()
            second = _memory_connection._schedule_embedder_warmup()
            assert first is True
            assert second is False, "second concurrent warm-up must be suppressed"
        finally:
            release.set()
            _memory_connection.get_embedder = orig  # type: ignore[assignment]
            t = _memory_connection._WARMUP_THREAD
            if t is not None:
                t.join(timeout=5)

    def test_warmup_skipped_when_embeddings_disabled(self) -> None:
        """No warm-up thread is started when embeddings are off."""
        from unittest.mock import patch

        from trw_mcp.state import _memory_connection

        _memory_connection._WARMUP_THREAD = None
        with patch(
            "trw_mcp.models.config.get_config",
            return_value=TRWConfig(embeddings_enabled=False),
        ):
            started = _memory_connection._schedule_embedder_warmup()
        assert started is False
        assert _memory_connection._WARMUP_THREAD is None

    def test_warmup_skipped_when_embedder_already_initialized(self) -> None:
        """No warm-up needed once the singleton is already checked/loaded."""
        from unittest.mock import patch

        from trw_mcp.state import _memory_connection

        _memory_connection._WARMUP_THREAD = None
        # Simulate "already initialized" state.
        _memory_connection._embedder_checked = True
        try:
            with patch(
                "trw_mcp.models.config.get_config",
                return_value=TRWConfig(embeddings_enabled=True),
            ):
                started = _memory_connection._schedule_embedder_warmup()
            assert started is False
            assert _memory_connection._WARMUP_THREAD is None
        finally:
            _memory_connection.reset_embedder()


class TestSessionStartWiresWarmup:
    """run_auto_maintenance kicks the warm-up when cold-init was deferred."""

    def test_maintenance_schedules_warmup_when_init_deferred(self, tmp_path: object) -> None:
        from unittest.mock import patch

        from trw_mcp.tools import _ceremony_helpers

        cfg = TRWConfig(embeddings_enabled=True)
        object.__setattr__(cfg, "run_auto_close_enabled", False)
        object.__setattr__(cfg, "auto_upgrade", False)
        object.__setattr__(cfg, "session_start_defer_under_writer_pressure", False)
        deferred_status = {
            "enabled": True,
            "available": False,
            "advisory": "",
            "initialization_deferred": True,
            "recent_failures": 0,
        }
        with (
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value=deferred_status,
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_embedder_warmup",
                return_value=True,
            ) as warmup,
        ):
            result = _ceremony_helpers.run_auto_maintenance(tmp_path / ".trw", cfg)  # type: ignore[operator]

        warmup.assert_called_once()
        assert result.get("embedder_warmup_scheduled") == {
            "reason": "first_recall_download_guard",
            "thread_started": True,
        }

    def test_maintenance_skips_warmup_when_already_available(self, tmp_path: object) -> None:
        from unittest.mock import patch

        from trw_mcp.tools import _ceremony_helpers

        cfg = TRWConfig(embeddings_enabled=True)
        object.__setattr__(cfg, "run_auto_close_enabled", False)
        object.__setattr__(cfg, "auto_upgrade", False)
        object.__setattr__(cfg, "session_start_defer_under_writer_pressure", False)
        # Embedder already loaded -> not deferred -> no warm-up needed.
        ready_status = {
            "enabled": True,
            "available": True,
            "advisory": "",
            "recent_failures": 0,
        }
        with (
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value=ready_status,
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_embedder_warmup",
                return_value=True,
            ) as warmup,
        ):
            result = _ceremony_helpers.run_auto_maintenance(tmp_path / ".trw", cfg)  # type: ignore[operator]

        warmup.assert_not_called()
        assert "embedder_warmup_scheduled" not in result


class TestLowCoverageAdvisoryIsOneTime:
    """The low-vector-coverage backfill nudge must not cry wolf every session."""

    def setup_method(self) -> None:
        from trw_mcp.tools import _ceremony_embeddings_maintenance as m

        m.reset_low_coverage_advisory_guard()

    def teardown_method(self) -> None:
        from trw_mcp.tools import _ceremony_embeddings_maintenance as m

        m.reset_low_coverage_advisory_guard()

    def _low_coverage_run(self, trw_dir: Path | None = None) -> object:
        from unittest.mock import patch

        from trw_mcp.tools import _ceremony_embeddings_maintenance as m

        cfg = TRWConfig(embeddings_enabled=True)
        low_status = {
            "enabled": True,
            "available": True,
            "advisory": "Vector coverage is low: 1/100 entries have embeddings (1.0%).",
            "coverage_ratio": 0.01,
            "recent_failures": 0,
        }
        maintenance: dict[str, object] = {}
        resolved_trw_dir = trw_dir or Path("/tmp/x/.trw")
        with (
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value=low_status,
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_post_recovery_backfill",
                return_value=True,
            ),
        ):
            m.run_embeddings_maintenance(
                resolved_trw_dir,
                cfg,
                maintenance,  # type: ignore[arg-type]
                census=_unpressured_census(),
                defer_memory_heavy=False,
            )
        return maintenance

    def test_advisory_surfaces_first_session_only(self) -> None:
        first = self._low_coverage_run()
        assert "embeddings_advisory" in first  # type: ignore[operator]
        # A background self-heal is still scheduled every session (idempotent),
        # but the human-facing advisory is suppressed after the first surfacing.
        assert "embeddings_backfill_scheduled" in first  # type: ignore[operator]

        second = self._low_coverage_run()
        assert "embeddings_advisory" not in second, (  # type: ignore[operator]
            "low-coverage advisory must be one-time per process, not cry wolf"
        )
        assert "embeddings_backfill_scheduled" in second  # type: ignore[operator]

    def test_advisory_guard_is_isolated_by_project(self, tmp_path: Path) -> None:
        first_project = self._low_coverage_run(tmp_path / "one" / ".trw")
        second_project = self._low_coverage_run(tmp_path / "two" / ".trw")

        assert "embeddings_advisory" in first_project  # type: ignore[operator]
        assert "embeddings_advisory" in second_project  # type: ignore[operator]


class TestEmbeddingsDeferralNarrowing:
    """PRD-CORE-257-FR08: pressure skips the background backfill SCHEDULE only."""

    def _pressured_census(self) -> WriterCensus:
        return WriterCensus(
            writer_pids=(1, 2, 3, 4),
            writer_count=4,
            peer_writer_count=3,
            threshold=3,
            under_pressure=True,
            census_state="measured",
            identity_state="verified",
            heartbeat_state="measured",
        )

    def test_embeddings_deferral_narrowed_to_backfill_schedule(self, tmp_path: Path) -> None:
        """The coverage probe and the warm-up run; only the self-heal is skipped.

        The deferral branch used to ``return`` before THREE downstream calls
        while reporting a single ``embeddings_backfill_deferred`` key: the
        read-only coverage probe (which populates ``embeddings_coverage_ratio``
        and the advisory), the PRD-FIX-105 first-recall warm-up (a thread spawn
        with no store write), and the post-recovery backfill schedule (the
        expensive database work). Reverting the narrowing turns the first two
        assertions red.
        """
        from unittest.mock import patch

        from trw_mcp.tools import _ceremony_embeddings_maintenance as m

        m.reset_low_coverage_advisory_guard()
        trw_dir = tmp_path / ".trw"
        (trw_dir / "runtime").mkdir(parents=True)
        cfg = TRWConfig(embeddings_enabled=True)
        probe_calls: list[bool] = []
        warmup_calls: list[int] = []
        backfill_calls: list[int] = []

        def _status(**kwargs: object) -> dict[str, object]:
            probe_calls.append(bool(kwargs.get("coverage_probe", False)))
            return {
                "enabled": True,
                "available": True,
                "initialization_deferred": True,
                "advisory": "Vector coverage is low: 1/100 entries have embeddings (1.0%).",
                "coverage_ratio": 0.01,
                "recent_failures": 0,
            }

        maintenance: dict[str, object] = {}
        with (
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", side_effect=_status),
            patch(
                "trw_mcp.state._memory_connection._schedule_embedder_warmup",
                side_effect=lambda: warmup_calls.append(1) or True,
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_post_recovery_backfill",
                side_effect=lambda _d: backfill_calls.append(1) or True,
            ),
        ):
            m.run_embeddings_maintenance(
                trw_dir,
                cfg,
                maintenance,  # type: ignore[arg-type]
                census=self._pressured_census(),
                defer_memory_heavy=True,
            )

        assert probe_calls == [True], "the read-only coverage probe must still run under pressure"
        assert len(warmup_calls) == 1, "the first-recall warm-up guard must still run under pressure"
        assert backfill_calls == [], "the expensive post-recovery backfill must be the ONLY skip"
        assert "embeddings_coverage_ratio" in maintenance
        assert "embeddings_advisory" in maintenance
        assert "embeddings_backfill_scheduled" not in maintenance
        deferred = maintenance["embeddings_backfill_deferred"]
        assert isinstance(deferred, dict)
        assert "backfill" in str(deferred["detail"]).lower()
        assert deferred["reason"] == "writer_pressure"
        assert "deferral_age_hours" in deferred
