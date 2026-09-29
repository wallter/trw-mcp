"""Tests for PRD-CORE-053: Auto-Checkpoint & Compaction Safety.

Covers:
- Config defaults for auto_checkpoint_enabled, auto_checkpoint_tool_interval,
  auto_checkpoint_pre_compact
- _maybe_auto_checkpoint: counter increment, interval trigger, disabled skip
- execute_pre_compact_checkpoint (PRD-CORE-300 S6a: trw_checkpoint(pre_compact=True)): active run, no run, disabled config
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
    execute_pre_compact_checkpoint,
)


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

    def test_auto_checkpoint_pre_compact_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.auto_checkpoint_pre_compact is True

    def test_config_fields_overridable(self) -> None:
        cfg = TRWConfig(auto_checkpoint_pre_compact=False)
        assert cfg.auto_checkpoint_pre_compact is False


# --- execute_pre_compact_checkpoint (trw_checkpoint(pre_compact=True) impl) ---


from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._layout import requires_non_root


class TestPreCompactCheckpoint:
    """execute_pre_compact_checkpoint -- the trw_checkpoint(pre_compact=True) implementation."""

    def test_creates_checkpoint_with_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """Active run -> creates pre-compaction safety checkpoint."""
        _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            result = execute_pre_compact_checkpoint(None)

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
        _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=None):
            result = execute_pre_compact_checkpoint(None)

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
        _make_ceremony_server(monkeypatch, tmp_path)

        result = execute_pre_compact_checkpoint(None)

        assert result["status"] == "skipped"
        assert "auto_checkpoint_pre_compact" in result["reason"]

    @requires_non_root
    def test_handles_checkpoint_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception during checkpoint -> returns failed status."""
        _make_ceremony_server(monkeypatch, tmp_path)

        with (
            patch(
                "trw_mcp.tools.checkpoint.find_active_run",
                return_value=Path("/nonexistent"),
            ),
        ):
            result = execute_pre_compact_checkpoint(None)

        assert result["status"] == "failed"
        assert "error" in result

    def test_event_logged_on_checkpoint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """Checkpoint should also log an event to events.jsonl."""
        _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
            execute_pre_compact_checkpoint(None)

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["event"] == "checkpoint"
        # The checkpoint event stores message at top level (via _events.log_event data dict)
        event_message = str(event.get("message", event.get("data", {}).get("message", "")))
        assert "pre-compaction" in event_message
