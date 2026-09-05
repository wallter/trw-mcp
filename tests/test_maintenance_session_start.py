"""Tests for session_start maintenance integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


class TestSessionStartAutoClose:
    """trw_session_start Step 5: auto_close_stale_runs integration."""

    @staticmethod
    def _get_session_start_fn() -> object:
        """Register ceremony tools on a minimal FastMCP server and return the tool."""
        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        tool = get_tools_sync(server)["trw_session_start"]
        return getattr(tool, "fn", tool)

    def test_session_start_calls_auto_close_when_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=True, auto_close_stale_runs is called and
        the result is surfaced in the return value when count > 0."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        import trw_mcp.state.analytics._stale_runs as stale_mod

        close_result = {"runs_closed": ["run-001"], "count": 1, "errors": []}
        original_fn = stale_mod.auto_close_stale_runs
        mock_close = MagicMock(return_value=close_result)

        fn = self._get_session_start_fn()

        try:
            stale_mod.auto_close_stale_runs = mock_close
            with (
                patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
                patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
                patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
                patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
                patch("trw_mcp.tools.ceremony._events"),
            ):
                result = fn()
        finally:
            stale_mod.auto_close_stale_runs = original_fn

        mock_close.assert_called_once()
        assert result.get("stale_runs_closed") == close_result

    def test_session_start_does_not_call_auto_close_when_disabled(
        self,
        tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=False, auto_close_stale_runs is never called."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", False)

        mock_close = MagicMock(return_value={"runs_closed": [], "count": 0, "errors": []})
        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
            patch("trw_mcp.state.analytics.report.auto_close_stale_runs", mock_close),
        ):
            fn = self._get_session_start_fn()
            result = fn()

        mock_close.assert_not_called()
        assert "stale_runs_closed" not in result

    def test_session_start_auto_close_exception_is_fail_open(
        self,
        tmp_path: Path,
    ) -> None:
        """If auto_close_stale_runs raises, session_start still succeeds."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
        ):
            import trw_mcp.state.analytics.report as ar_mod

            original_fn = ar_mod.auto_close_stale_runs
            try:
                ar_mod.auto_close_stale_runs = MagicMock(side_effect=RuntimeError("disk full"))
                fn = self._get_session_start_fn()
                result = fn()
            finally:
                ar_mod.auto_close_stale_runs = original_fn

        assert result is not None
        assert "stale_runs_closed" not in result

    def test_session_start_surfaces_scheduled_embedding_backfill(self, tmp_path: Path) -> None:
        """The public compact result preserves actionable maintenance remediation."""
        cfg = TRWConfig()
        scheduled = {"reason": "low_coverage", "thread_started": True}

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
            patch(
                "trw_mcp.tools._ceremony_helpers.step_sanitize_and_maintain",
                return_value={"embeddings_backfill_scheduled": scheduled},
            ),
        ):
            result = self._get_session_start_fn()()

        assert result["embeddings_backfill_scheduled"] == scheduled


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR03 wiring proof: the seams are reached from the production drain
# ---------------------------------------------------------------------------


class TestSweepLevelCosts:
    """The per-record costs must actually become per-sweep on the real path.

    NON-VACUITY: this counts invocations through the production drain step, not
    through a helper. Stop passing the index sink and the shared active set in
    ``_learn_journal_wiring.make_sweep_replay`` and both counters go to 5.
    """

    def test_sweep_does_one_index_write_and_one_active_listing(self, tmp_path: Path, monkeypatch: object) -> None:
        from trw_mcp.state import learn_journal, memory_adapter
        from trw_mcp.state.analytics import entries as entries_mod
        from trw_mcp.state.memory_pressure import take_writer_census
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False, learn_journal_drain_budget_ms=120_000)
        for i in range(5):
            learn_journal.journal_pending(
                trw_dir,
                f"L-sweep{i:03d}",
                {
                    "summary": f"sweep-level cost probe record {i} with a summary past the noise gate",
                    "detail": f"detail body for sweep-level cost probe record {i}",
                    "impact": 0.5,
                },
            )

        index_writes: list[Path] = []
        active_listings: list[int] = []
        real_write = entries_mod.FileStateWriter.write_yaml
        real_list = memory_adapter.list_active_learnings

        def _counting_write(self: object, path: Path, data: object) -> None:
            if path.name == "index.yaml":
                index_writes.append(path)
            real_write(self, path, data)  # type: ignore[arg-type]

        def _counting_list(*args: object, **kwargs: object) -> object:
            active_listings.append(1)
            return real_list(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", _counting_write)  # type: ignore[attr-defined]
        monkeypatch.setattr(memory_adapter, "list_active_learnings", _counting_list)  # type: ignore[attr-defined]

        steps._DRAIN_THREAD = None
        maintenance: dict[str, object] = {}
        steps._run_learn_journal_drain(
            trw_dir,
            config,
            maintenance,  # type: ignore[arg-type]
            census=take_writer_census(trw_dir, threshold=2),
            defer_memory_heavy=False,
        )
        thread = steps._DRAIN_THREAD
        if thread is not None:
            thread.join(60.0)
        steps._DRAIN_THREAD = None

        assert learn_journal.pending_count(trw_dir) == 0
        assert len(index_writes) == 1, f"expected 1 index write for a 5-record sweep, got {len(index_writes)}"
        assert len(active_listings) == 1, (
            f"expected 1 active-set materialization for a 5-record sweep, got {len(active_listings)}"
        )

    def test_a_budget_split_sweep_still_does_one_index_write_and_one_listing(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        """FIX130-04: the inline phase and the continuation share ONE sweep context.

        FR03 promises one index read-modify-write and one active-set
        materialization PER SWEEP. The first implementation built a second
        ``make_sweep_replay`` inside the background continuation, so exactly the
        runs FR02 exists for — a sweep the wall-clock budget SPLIT — paid both
        costs twice. The original FR03 test used a 120-second budget, which
        guaranteed no split and so could never see it.

        NON-VACUITY: give the continuation its own context again and both
        counters go to 2.
        """
        from trw_mcp.state import learn_journal, memory_adapter
        from trw_mcp.state.analytics import entries as entries_mod
        from trw_mcp.state.memory_pressure import take_writer_census
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        # A PARTIALLY split sweep: one record inline, three on the continuation.
        # A budget of 0 would not expose the defect — the inline phase replays
        # nothing, so its (unshared) context has an empty sink and costs nothing.
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False, learn_journal_drain_budget_ms=3000)
        for i in range(4):
            learn_journal.journal_pending(
                trw_dir,
                f"L-split{i:03d}",
                {
                    "summary": f"split-sweep cost probe record {i} with a summary past the noise gate",
                    "detail": f"detail body for split-sweep cost probe record {i}",
                    "impact": 0.5,
                },
            )

        index_writes: list[Path] = []
        active_listings: list[int] = []
        real_write = entries_mod.FileStateWriter.write_yaml
        real_list = memory_adapter.list_active_learnings

        def _counting_write(self: object, path: Path, data: object) -> None:
            if path.name == "index.yaml":
                index_writes.append(path)
            real_write(self, path, data)  # type: ignore[arg-type]

        def _counting_list(*args: object, **kwargs: object) -> object:
            active_listings.append(1)
            return real_list(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", _counting_write)  # type: ignore[attr-defined]
        monkeypatch.setattr(memory_adapter, "list_active_learnings", _counting_list)  # type: ignore[attr-defined]

        # A scripted clock, so the split point is exact and no assertion here
        # depends on wall time: inside the budget for record 1, past it for the
        # deadline check before record 2.
        ticks = iter([0.0, 0.0])

        def _clock() -> float:
            return next(ticks, 100.0)

        monkeypatch.setattr(learn_journal.time, "monotonic", _clock)  # type: ignore[attr-defined]

        steps._DRAIN_THREAD = None
        maintenance: dict[str, object] = {}
        steps._run_learn_journal_drain(
            trw_dir,
            config,
            maintenance,  # type: ignore[arg-type]
            census=take_writer_census(trw_dir, threshold=2),
            defer_memory_heavy=False,
        )
        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        assert payload["replayed_inline"] == 1, payload
        assert payload["deferred_to_background"] == 3, payload
        thread = steps._DRAIN_THREAD
        if thread is not None:
            thread.join(60.0)
            assert not thread.is_alive()
        steps._DRAIN_THREAD = None

        assert learn_journal.pending_count(trw_dir) == 0
        assert len(index_writes) == 1, f"a SPLIT sweep did {len(index_writes)} index writes, not 1"
        assert len(active_listings) == 1, f"a SPLIT sweep did {len(active_listings)} active listings, not 1"
