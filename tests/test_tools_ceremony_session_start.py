"""Integration tests for trw_session_start ceremony flows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


@pytest.mark.integration
class TestSessionStartPartialFailure:
    """trw_session_start resilience when sub-operations fail."""

    def test_returns_result_when_recall_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If recall raises, status step still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                return_value=None,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert len(result["errors"]) >= 1
        assert "recall" in result["errors"][0]
        assert "run" in result

    def test_returns_result_when_status_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If status check raises, recall still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                side_effect=Exception("status boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert any("status" in error for error in result["errors"])
        assert "learnings" in result

    def test_success_when_all_steps_work(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both recall and status succeed."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert result["errors"] == []
        assert "timestamp" in result

    def test_session_start_repopulates_injected_learning_ids(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """surfaced session_start learnings must seed the injected-ID state file."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        injected_file = trw_dir / "context" / "injected_learning_ids.txt"

        surfaced = [
            {"id": "L-session-1", "summary": "First surfaced learning"},
            {"id": "L-session-2", "summary": "Second surfaced learning"},
        ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
                return_value=(surfaced, False, {}),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert injected_file.read_text(encoding="utf-8").splitlines() == [
            "L-session-1",
            "L-session-2",
        ]

    def test_session_start_returns_assertion_health(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Assertion health is exposed through the production trw_session_start tool path."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_backend = MagicMock()
        mock_backend.entries_with_assertions.return_value = [
            MagicMock(
                assertions=[
                    MagicMock(last_result=True, last_verified_at=recent),
                    MagicMock(last_result=False, last_verified_at=recent),
                ]
            ),
            MagicMock(
                assertions=[
                    MagicMock(last_result=None, last_verified_at=None),
                    MagicMock(last_result=None, last_verified_at=recent),
                ]
            ),
        ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend),
        ):
            # verbose=True: assertion_health is a diagnostic sub-block that
            # compact-by-default (PRD-IMPROVE-MCP-04) folds into health_summary.
            result = tools["trw_session_start"].fn(verbose=True)

        assert result["success"] is True
        assert result["assertion_health"] == {
            "passing": 1,
            "failing": 1,
            "stale": 1,
            "unverifiable": 1,
            "total": 2,
        }


@pytest.mark.integration
class TestSessionStartUpdateAdvisory:
    """Verify check_for_update() wiring in trw_session_start."""

    def test_update_advisory_included_when_update_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=True, advisory is in results."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": True,
                    "current": "0.4.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": "TRW v0.5.0 available (you have v0.4.0). ",
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "update_advisory" in result
        assert "0.5.0" in str(result["update_advisory"])

    def test_no_update_advisory_when_up_to_date(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=False, advisory key is absent."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": False,
                    "current": "0.5.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": None,
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result.get("update_advisory") is None

    def test_update_check_failure_is_fail_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If check_for_update raises, session start still succeeds."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                side_effect=Exception("network boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "update_advisory" not in result or result.get("update_advisory") is None


@pytest.mark.integration
class TestSessionStartPayloadTrimming:
    """PRD-IMPROVE-MCP-04 FR1 — compact-by-default vs verbose, end-to-end."""

    def test_default_is_compact_with_health_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        # Compact mode: flag set, diagnostic blocks folded into a summary,
        # token estimate present, load-bearing fields intact.
        assert result["compact"] is True
        assert "health_summary" in result
        assert "embed_health" not in result
        assert "step_durations_ms" not in result
        assert isinstance(result["payload_token_estimate"], int)
        assert result["payload_token_estimate"] > 0
        assert "run" in result
        assert "framework_reminder" in result
        assert "errors" in result

    def test_verbose_returns_full_payload(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn(verbose=True)

        # Verbose mode: full diagnostic payload, no summary collapse.
        assert result["compact"] is False
        assert "health_summary" not in result
        assert "embed_health" in result
        assert "step_durations_ms" in result
        # Run/pin + framework reminder still present.
        assert "run" in result
        assert "framework_reminder" in result
        assert "timestamp" in result
