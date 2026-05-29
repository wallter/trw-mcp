"""Tests for deliver maintenance integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


class TestDeliverAutoPrune:
    """trw_deliver Step 2.5: auto_prune_excess_entries integration."""

    def _make_deliver_fn(self) -> object:
        """Register ceremony tools and return the trw_deliver callable."""
        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        return get_tools_sync(server)["trw_deliver"].fn

    def test_deliver_calls_auto_prune_when_enabled(self, tmp_path: Path) -> None:
        """When learning_auto_prune_on_deliver=True, auto_prune_excess_entries is invoked.

        Auto-prune is a deferred step — test it via _run_deferred_steps directly.
        """
        from trw_mcp.tools._deferred_delivery import _run_deferred_steps

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 150)

        prune_result = {"actions_taken": 5, "status": "pruned"}
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        noop: dict[str, object] = {"status": "skipped"}
        mock_prune = MagicMock(return_value=prune_result)

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        ):
            import trw_mcp.state.analytics as analytics_mod_state

            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune
                _run_deferred_steps(trw_dir, None, {})
            finally:
                analytics_mod_state.auto_prune_excess_entries = original

        mock_prune.assert_called_once()

        import json

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert "auto_prune" in entry["results"]

    def test_deliver_does_not_call_auto_prune_when_disabled(
        self,
        tmp_path: Path,
    ) -> None:
        """When learning_auto_prune_on_deliver=False, auto_prune_excess_entries is not called."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", False)

        fn = self._make_deliver_fn()
        mock_prune = MagicMock(return_value={"actions_taken": 0})

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={
                    "status": "success",
                    "events_analyzed": 0,
                    "learnings_produced": 0,
                    "success_patterns": 0,
                },
            ),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0},
            ),
            patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value={"status": "success"}),
            patch("trw_mcp.tools._deferred_delivery._do_auto_progress", return_value={"status": "skipped"}),
        ):
            import trw_mcp.state.analytics as analytics_mod_state

            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune
                fn(skip_reflect=False, skip_index_sync=False)
            finally:
                analytics_mod_state.auto_prune_excess_entries = original

        mock_prune.assert_not_called()

    def test_deliver_auto_prune_exception_is_fail_open(
        self,
        tmp_path: Path,
    ) -> None:
        """If auto_prune_excess_entries raises, deferred steps still continue.

        Auto-prune is a deferred step — test via _run_deferred_steps directly.
        """
        from trw_mcp.tools._deferred_delivery import _run_deferred_steps

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 150)

        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        noop: dict[str, object] = {"status": "skipped"}

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        ):
            import trw_mcp.state.analytics as analytics_mod_state

            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = MagicMock(side_effect=RuntimeError("storage error"))
                _run_deferred_steps(trw_dir, None, {})
            finally:
                analytics_mod_state.auto_prune_excess_entries = original

        import json

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["results"]["auto_prune"]["status"] == "failed"
        assert not entry["success"]

    def test_deliver_auto_prune_cap_passed_correctly(
        self,
        tmp_path: Path,
    ) -> None:
        """auto_prune_excess_entries is called with max_entries=config.learning_auto_prune_cap."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 200)

        fn = self._make_deliver_fn()
        mock_prune = MagicMock(return_value={"actions_taken": 0})

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={
                    "status": "success",
                    "events_analyzed": 0,
                    "learnings_produced": 0,
                    "success_patterns": 0,
                },
            ),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0},
            ),
            patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value={"status": "success"}),
            patch("trw_mcp.tools._deferred_delivery._do_auto_progress", return_value={"status": "skipped"}),
        ):
            import trw_mcp.state.analytics as analytics_mod_state

            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune
                fn(skip_reflect=False, skip_index_sync=False)
            finally:
                analytics_mod_state.auto_prune_excess_entries = original

        call_kwargs = mock_prune.call_args
        assert call_kwargs is not None
        args, kwargs = call_kwargs
        passed_cap = kwargs.get("max_entries") or (args[1] if len(args) > 1 else None)
        assert passed_cap == 200
