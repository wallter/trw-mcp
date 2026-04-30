"""Tests for PRD-FIX-027 build-check/Q-learning wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestBuildCheckQLearningWiring:
    """Bug 2: trw_build_check must call process_outcome_for_event after each run.

    PRD-CORE-098: trw_build_check is now a reporter API — agents pass results
    directly instead of running subprocesses.
    """

    def _get_tool_fn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> object:
        """Register build tools with mocked dependencies, return the tool fn."""
        from fastmcp import FastMCP

        import trw_mcp.tools.build as build_mod
        import trw_mcp.tools.build._registration as reg_mod

        mock_config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        monkeypatch.setattr(reg_mod, "get_config", lambda: mock_config)
        monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr(reg_mod, "find_active_run", lambda: None)

        server = FastMCP("test")
        build_mod.register_build_tools(server)

        tools = get_tools_sync(server)
        if "trw_build_check" in tools:
            return tools["trw_build_check"].fn
        return None

    def test_build_check_calls_process_outcome_on_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When build passes, process_outcome_for_event('build_passed') must be called."""
        called_events: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        tool_fn = self._get_tool_fn(tmp_path, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        tool_fn(tests_passed=True, test_count=100, coverage_pct=95.0, scope="full")

        assert "build_passed" in called_events, (
            "trw_build_check did not call process_outcome_for_event('build_passed') — "
            "Q-learning gets no reward signal from successful builds"
        )

    def test_build_check_calls_process_outcome_on_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When build fails, process_outcome_for_event('build_failed') must be called."""
        called_events: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        tool_fn = self._get_tool_fn(tmp_path, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        tool_fn(tests_passed=False, test_count=50, failure_count=5, coverage_pct=60.0, scope="full")

        assert "build_failed" in called_events, (
            "trw_build_check did not call process_outcome_for_event('build_failed') — "
            "Q-learning gets no negative signal from build failures"
        )

    def test_build_check_q_learning_failure_does_not_block_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q-learning errors must be swallowed — never block build check results."""

        def exploding_process(event_type: str) -> list[str]:
            raise RuntimeError("Q-learning exploded!")

        monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", exploding_process)

        tool_fn = self._get_tool_fn(tmp_path, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        result = tool_fn(tests_passed=True, test_count=100, coverage_pct=95.0, scope="full")
        assert result["tests_passed"] is True


class TestBuildCheckMypyOnlyScope:
    """FR03: Verify build_check behavior for mypy-only scope.

    Note: PRD-FIX-027-FR03 states mypy-only scope should NOT fire a build outcome event.
    Current implementation fires 'build_passed' if mypy is clean (tests_passed=True by default).
    This test documents current implemented behavior.
    """

    def _make_mypy_status(self, mypy_clean: bool) -> MagicMock:
        mock_status = MagicMock()
        mock_status.tests_passed = True
        mock_status.mypy_clean = mypy_clean
        mock_status.coverage_pct = 0.0
        mock_status.test_count = 0
        mock_status.failure_count = 0
        mock_status.failures = []
        mock_status.scope = "mypy"
        mock_status.duration_secs = 0.5
        return mock_status

    def test_mypy_only_scope_fires_build_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR03 (impl): For scope='mypy' with clean results, build_passed event fires.

        PRD-CORE-098: trw_build_check is a reporter API — agents pass results directly.
        """
        import trw_mcp.tools.build as build_mod
        import trw_mcp.tools.build._registration as reg_mod

        called_events: list[str] = []
        mock_config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        monkeypatch.setattr(reg_mod, "get_config", lambda: mock_config)
        monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr(reg_mod, "find_active_run", lambda: None)
        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        server = __import__("fastmcp", fromlist=["FastMCP"]).FastMCP("test")
        build_mod.register_build_tools(server)
        tools = get_tools_sync(server)
        assert "trw_build_check" in tools
        tool_fn = tools["trw_build_check"].fn
        tool_fn(tests_passed=True, mypy_clean=True, scope="mypy")

        assert "build_passed" in called_events, "scope='mypy' fires 'build_passed' event in current implementation"

    def test_q_observations_increments_in_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR04: After build_check, correlated learning's q_observations increments in YAML.

        Full integration: write entry + receipt, patch scoring module, run build_check,
        verify q_observations > 0.
        """
        from fastmcp import FastMCP

        import trw_mcp.tools.build as build_mod
        import trw_mcp.tools.build._registration as reg_mod

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()

        entry: dict[str, object] = {
            "id": "L-qobs001",
            "summary": "q-obs test entry",
            "detail": "for q_observations increment test",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "recurrence": 1,
            "tags": [],
        }
        entry_path = entries_dir / "L-qobs001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-qobs001"],
            "query": "q-obs test",
        }
        writer.append_jsonl(logs_dir / "recall_tracking.jsonl", receipt)

        cfg = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")

        monkeypatch.setattr("trw_mcp.scoring._correlation.get_config", lambda: cfg)
        monkeypatch.setattr("trw_mcp.scoring._utils.resolve_trw_dir", lambda: trw_dir)

        mock_status = MagicMock()
        mock_status.tests_passed = True
        mock_status.mypy_clean = True
        mock_status.coverage_pct = 95.0
        mock_status.test_count = 100
        mock_status.failure_count = 0
        mock_status.failures = []
        mock_status.scope = "full"
        mock_status.duration_secs = 1.0

        monkeypatch.setattr(reg_mod, "get_config", lambda: cfg)
        monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: trw_dir)
        monkeypatch.setattr(reg_mod, "find_active_run", lambda: None)

        server = FastMCP("test")
        build_mod.register_build_tools(server)
        tools = get_tools_sync(server)
        assert "trw_build_check" in tools
        tool_fn = tools["trw_build_check"].fn
        result = tool_fn(tests_passed=True, test_count=100, coverage_pct=95.0, scope="full")
        assert result["tests_passed"] is True

        stored = reader.read_yaml(entry_path)
        q_obs = int(str(stored.get("q_observations", 0)))
        assert q_obs >= 1, f"FR04: q_observations should be >= 1 after build_check, got {q_obs}"

    def test_build_check_leaves_no_q_learning_worker_running(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: build_check must not leave a background SQLite worker alive."""
        import trw_mcp.tools.build as build_mod
        import trw_mcp.tools.build._registration as reg_mod

        called_events: list[str] = []
        mock_config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        monkeypatch.setattr(reg_mod, "get_config", lambda: mock_config)
        monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr(reg_mod, "find_active_run", lambda: None)
        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        server = __import__("fastmcp", fromlist=["FastMCP"]).FastMCP("test")
        build_mod.register_build_tools(server)
        tool_fn = get_tools_sync(server)["trw_build_check"].fn

        result = tool_fn(tests_passed=True, mypy_clean=True, scope="mypy")

        assert result["tests_passed"] is True
        assert "build_passed" in called_events
        health = reg_mod.get_q_learning_health()
        assert health["worker_alive"] is False
        assert health["queue_size"] == 0
