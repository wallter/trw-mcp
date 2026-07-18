"""Tests for PRD-CORE-084 context-aware session_start responses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig


class TestSessionStartLightMode:
    """FR07: session_start uses compact response for light mode."""

    def _invoke_session_start(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ceremony_mode: str,
    ) -> dict[str, object]:
        """Invoke trw_session_start via the MCP tool path with mocked infra."""
        from tests.conftest import get_tools_sync, make_test_server

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)

        cfg = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(cfg, "ceremony_mode", ceremony_mode)

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        server = make_test_server("ceremony")
        tools = get_tools_sync(server)

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch("trw_mcp.tools._ceremony_helpers.get_config", create=True, return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            result: dict[str, object] = tools["trw_session_start"].fn(query="")
        return result

    def test_light_mode_framework_reminder_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Light mode framework_reminder says 'Call trw_deliver() when done'."""
        result = self._invoke_session_start(monkeypatch, tmp_path, "light")
        reminder = str(result.get("framework_reminder", ""))
        assert "trw_deliver()" in reminder
        assert "FRAMEWORK-CORE.md" not in reminder

    def test_full_mode_framework_reminder_mentions_framework(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Full mode framework_reminder references FRAMEWORK-CORE.md."""
        result = self._invoke_session_start(monkeypatch, tmp_path, "full")
        reminder = str(result.get("framework_reminder", ""))
        assert "FRAMEWORK-CORE.md" in reminder

    def test_light_mode_skips_ceremony_nudge(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """In light mode, ceremony_status nudge is not injected into session_start."""
        result = self._invoke_session_start(monkeypatch, tmp_path, "light")
        assert "ceremony_status" not in result

    def test_full_mode_includes_ceremony_nudge(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """In full mode, ceremony_status nudge IS injected."""
        result = self._invoke_session_start(monkeypatch, tmp_path, "full")
        reminder = str(result.get("framework_reminder", ""))
        assert "FRAMEWORK-CORE.md" in reminder
