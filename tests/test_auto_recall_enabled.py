"""Enabled auto-recall behavior tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._test_auto_recall_support import _setup_trw_dir
from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.state.memory_adapter import get_backend, store_learning


class TestAutoRecallEnabled:
    """Auto-recall is active by default and surfaces entries."""

    def test_auto_recall_returns_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When auto-recall is enabled, returned entries appear in result."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        mock_entries = [
            {"id": "L-a1", "summary": "Learning A", "impact": 0.8, "tags": ["testing"], "status": "active"},
            {"id": "L-b2", "summary": "Learning B", "impact": 0.7, "tags": ["gotcha"], "status": "active"},
            {"id": "L-c3", "summary": "Learning C", "impact": 0.6, "tags": ["pattern"], "status": "active"},
        ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=mock_entries,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        assert result["auto_recall_count"] == 3

    def test_auto_recalled_entries_increment_session_counts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-recalled learnings count as surfaced via session_start."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)
        increment_calls: list[list[str]] = []
        recall_calls = {"count": 0}

        def _fake_increment(_trw_dir: Path, learning_ids: list[str]) -> None:
            increment_calls.append(list(learning_ids))

        def _fake_recall(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
            recall_calls["count"] += 1
            if recall_calls["count"] == 1:
                return []
            return [
                {"id": "L-auto-1", "summary": "Learning A", "impact": 0.8, "tags": ["testing"], "status": "active"},
                {"id": "L-auto-2", "summary": "Learning B", "impact": 0.7, "tags": ["gotcha"], "status": "active"},
            ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
            patch("trw_mcp.state.memory_adapter.increment_session_counts", side_effect=_fake_increment),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        assert increment_calls == [["L-auto-1", "L-auto-2"]]

    def test_session_start_then_repeated_recall_keeps_session_count_at_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Repeated recalls within one session do not bump session_count beyond the session-start surface."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        tools = get_tools_sync(make_test_server("ceremony", "learning", "checkpoint", "review"))
        trw_dir = _setup_trw_dir(tmp_path)
        learning_id = store_learning(trw_dir, "L-core119a", "Session count learning", "Detail")["learning_id"]
        surfaced = [{"id": learning_id, "summary": "Session count learning", "impact": 0.8, "tags": ["testing"]}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=surfaced),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            tools["trw_session_start"].fn()

        for _ in range(3):
            tools["trw_recall"].fn(query="Session count learning")

        entry = get_backend(trw_dir).get(learning_id)
        assert entry is not None
        assert entry.session_count == 1
        assert entry.access_count >= 3

    def test_three_session_starts_increment_session_count_to_three(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Distinct session starts count as distinct session surfaces."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        tools = get_tools_sync(make_test_server("ceremony", "learning", "checkpoint", "review"))
        trw_dir = _setup_trw_dir(tmp_path)
        learning_id = store_learning(trw_dir, "L-core119b", "Multi-session learning", "Detail")["learning_id"]
        surfaced = [{"id": learning_id, "summary": "Multi-session learning", "impact": 0.8, "tags": ["testing"]}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=surfaced),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            for _ in range(3):
                tools["trw_session_start"].fn()

        entry = get_backend(trw_dir).get(learning_id)
        assert entry is not None
        assert entry.session_count == 3

    def test_auto_recalled_duplicates_primary_ids_are_not_double_counted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only auto-recall IDs not already surfaced by primary recall are counted."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)
        surface_calls: list[list[str]] = []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
                return_value=(
                    [{"id": "L-shared", "summary": "Shared learning", "impact": 0.9}],
                    [],
                    {},
                ),
            ),
            patch(
                "trw_mcp.tools._ceremony_helpers._phase_contextual_recall",
                return_value=[
                    {"id": "L-shared", "summary": "Shared learning", "impact": 0.9},
                    {"id": "L-auto-new", "summary": "New auto learning", "impact": 0.8},
                ],
            ),
            patch(
                "trw_mcp.tools._ceremony_helpers.record_session_start_surfaces",
                side_effect=lambda _trw_dir, learning_ids: (
                    surface_calls.append(list(learning_ids)) or list(learning_ids)
                ),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["auto_recall_count"] == 2
        assert surface_calls == [["L-auto-new"]]

    def test_auto_recall_no_results_no_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When auto-recall returns empty list, auto_recalled key is absent."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=[],
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" not in result
        assert result.get("auto_recall_count") is None
