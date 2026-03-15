"""Tests for PRD-CORE-053: Auto-Checkpoint & Compaction Safety.

Covers:
- Config defaults for auto_checkpoint_enabled, auto_checkpoint_tool_interval,
  auto_checkpoint_pre_compact
- _maybe_auto_checkpoint: counter increment, interval trigger, disabled skip
- trw_pre_compact_checkpoint: active run, no run, disabled config
- Counter reset behavior via _reset_tool_call_counter
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.tools.checkpoint import (
    _maybe_auto_checkpoint,
    _reset_tool_call_counter,
)

# --- Fixtures ---


@pytest.fixture(autouse=True)
def _clean_counter() -> Any:
    """Reset the tool call counter before and after each test."""
    _reset_tool_call_counter()
    yield
    _reset_tool_call_counter()


@pytest.fixture(autouse=True)
def _clean_config() -> Any:
    """Reset config singleton after each test."""
    yield
    _reset_config()


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure with checkpoints file."""
    d = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- Config defaults ---


class TestAutoCheckpointConfigDefaults:
    """Verify PRD-CORE-053 config fields exist with correct defaults."""

    def test_auto_checkpoint_enabled_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.auto_checkpoint_enabled is True

    def test_auto_checkpoint_tool_interval_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.auto_checkpoint_tool_interval == 25

    def test_auto_checkpoint_pre_compact_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.auto_checkpoint_pre_compact is True

    def test_config_fields_overridable(self) -> None:
        cfg = TRWConfig(
            auto_checkpoint_enabled=False,
            auto_checkpoint_tool_interval=10,
            auto_checkpoint_pre_compact=False,
        )
        assert cfg.auto_checkpoint_enabled is False
        assert cfg.auto_checkpoint_tool_interval == 10
        assert cfg.auto_checkpoint_pre_compact is False


# --- _maybe_auto_checkpoint ---


class TestMaybeAutoCheckpoint:
    """Tool call counter and interval-based auto-checkpoint triggering."""

    def test_triggers_at_interval(self, run_dir: Path) -> None:
        """Counter reaches configured interval -> checkpoint is created."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=5)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            # Calls 1-4 should return None
            for _ in range(4):
                result = _maybe_auto_checkpoint()
                assert result is None

            # Call 5 should trigger
            result = _maybe_auto_checkpoint()

        assert result is not None
        assert result["auto_checkpoint"] is True
        assert result["tool_calls"] == 5

        # Verify checkpoint was written
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text(encoding="utf-8").strip())
        assert "auto-checkpoint after 5 tool calls" in data["message"]

    def test_skips_between_intervals(self, run_dir: Path) -> None:
        """Counter not at interval -> returns None, no checkpoint."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=10)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            for _ in range(9):
                result = _maybe_auto_checkpoint()
                assert result is None

        # No checkpoint should have been written
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert not cp_path.exists()

    def test_disabled_via_config(self, run_dir: Path) -> None:
        """Config disabled -> never triggers regardless of count."""
        cfg = TRWConfig(auto_checkpoint_enabled=False, auto_checkpoint_tool_interval=1)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            for _ in range(5):
                result = _maybe_auto_checkpoint()
                assert result is None

    def test_no_active_run(self) -> None:
        """No active run -> returns None even at interval."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=1)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=None):
            result = _maybe_auto_checkpoint()
        assert result is None

    def test_exception_in_checkpoint_is_swallowed(self) -> None:
        """Exceptions during checkpoint are caught (best-effort)."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=1)
        _reset_config(cfg)

        with (
            patch("trw_mcp.tools.checkpoint.find_active_run", side_effect=OSError("boom")),
        ):
            result = _maybe_auto_checkpoint()
        assert result is None

    def test_triggers_multiple_times(self, run_dir: Path) -> None:
        """Counter triggers at every multiple of the interval."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=3)
        _reset_config(cfg)

        triggered: list[dict[str, object]] = []
        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            for _ in range(9):
                result = _maybe_auto_checkpoint()
                if result is not None:
                    triggered.append(result)

        assert len(triggered) == 3
        assert triggered[0]["tool_calls"] == 3
        assert triggered[1]["tool_calls"] == 6
        assert triggered[2]["tool_calls"] == 9

    def test_zero_interval_never_triggers(self) -> None:
        """Zero interval value -> never triggers (guard against division by zero)."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=0)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=None):
            for _ in range(5):
                result = _maybe_auto_checkpoint()
                assert result is None


# --- _reset_tool_call_counter ---


class TestResetToolCallCounter:
    """Counter reset behavior."""

    def test_reset_clears_counter(self, run_dir: Path) -> None:
        """After reset, counter starts from zero again."""
        cfg = TRWConfig(auto_checkpoint_enabled=True, auto_checkpoint_tool_interval=3)
        _reset_config(cfg)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            # Count to 2
            _maybe_auto_checkpoint()
            _maybe_auto_checkpoint()

            # Reset
            _reset_tool_call_counter()

            # Count to 3 again — should trigger at the new 3rd call
            _maybe_auto_checkpoint()
            _maybe_auto_checkpoint()
            result = _maybe_auto_checkpoint()

        assert result is not None
        assert result["tool_calls"] == 3


# --- trw_pre_compact_checkpoint (MCP tool) ---


from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


class TestPreCompactCheckpoint:
    """trw_pre_compact_checkpoint MCP tool."""

    def test_creates_checkpoint_with_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """Active run -> creates pre-compaction safety checkpoint."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            result = tools["trw_pre_compact_checkpoint"].fn()

        assert result["status"] == "success"
        assert result["run_path"] == str(run_dir)

        # Verify checkpoint was written
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text(encoding="utf-8").strip())
        assert data["message"] == "pre-compaction safety checkpoint"

    def test_skips_without_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No active run -> returns skip status."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=None):
            result = tools["trw_pre_compact_checkpoint"].fn()

        assert result["status"] == "skipped"
        assert result["reason"] == "no_active_run"

    def test_skips_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Config auto_checkpoint_pre_compact=False -> returns skip status."""
        cfg = TRWConfig(auto_checkpoint_pre_compact=False)
        _reset_config(cfg)
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        result = tools["trw_pre_compact_checkpoint"].fn()

        assert result["status"] == "skipped"
        assert "auto_checkpoint_pre_compact" in result["reason"]

    def test_handles_checkpoint_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception during checkpoint -> returns failed status."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with (
            patch(
                "trw_mcp.tools.checkpoint.find_active_run",
                return_value=Path("/nonexistent"),
            ),
        ):
            result = tools["trw_pre_compact_checkpoint"].fn()

        assert result["status"] == "failed"
        assert "error" in result

    def test_event_logged_on_checkpoint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """Checkpoint should also log an event to events.jsonl."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            tools["trw_pre_compact_checkpoint"].fn()

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["event"] == "checkpoint"
        # The checkpoint event stores message at top level (via _events.log_event data dict)
        event_message = str(event.get("message", event.get("data", {}).get("message", "")))
        assert "pre-compaction" in event_message
