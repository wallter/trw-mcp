"""Tests for PRD-CORE-049: Phase-Contextual Auto-Recall.

Covers:
- FR01: Auto-recall in trw_session_start (config fields, search integration)
- FR02: Phase-to-tags mapping helper
- Fail-open behavior when search raises
- Config toggle to disable auto-recall
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# --- Fixtures ---
from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.state.memory_adapter import get_backend, store_learning
from trw_mcp.tools._ceremony_helpers import _phase_to_tags


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create minimal .trw/ directory structure for tests."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


# --- FR02: _phase_to_tags ---


class TestPhaseToTags:
    """Phase-to-tags mapping helper function."""

    def test_research_phase(self) -> None:
        tags = _phase_to_tags("research")
        assert tags == ["architecture", "gotcha", "codebase"]

    def test_implement_phase(self) -> None:
        tags = _phase_to_tags("implement")
        assert tags == ["gotcha", "testing", "pattern"]

    def test_validate_phase(self) -> None:
        tags = _phase_to_tags("validate")
        assert tags == ["testing", "build", "coverage"]

    def test_review_phase(self) -> None:
        tags = _phase_to_tags("review")
        assert tags == ["security", "performance", "maintainability"]

    def test_unknown_phase_returns_empty(self) -> None:
        assert _phase_to_tags("unknown") == []

    def test_empty_phase_returns_empty(self) -> None:
        assert _phase_to_tags("") == []

    def test_plan_phase_returns_tags(self) -> None:
        """plan phase maps to architecture, pattern, dependency tags."""
        assert _phase_to_tags("plan") == ["architecture", "pattern", "dependency"]

    def test_deliver_phase_returns_tags(self) -> None:
        """deliver phase maps to ceremony, deployment, integration tags."""
        assert _phase_to_tags("deliver") == ["ceremony", "deployment", "integration"]


# --- FR01: Auto-recall in trw_session_start ---


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

        def _fake_recall(*_args: Any, **_kwargs: Any) -> list[dict[str, object]]:
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
                side_effect=lambda _trw_dir, learning_ids: surface_calls.append(list(learning_ids)) or list(learning_ids),
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


class TestAutoRecallDisabled:
    """Auto-recall disabled via config flag."""

    def test_no_auto_recall_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When auto_recall_enabled=False, no auto_recalled key in result."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        disabled_config = TRWConfig(auto_recall_enabled=False)
        _reset_config(disabled_config)

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" not in result


class TestAutoRecallWithActiveRun:
    """Auto-recall uses task context from active run."""

    def test_uses_task_and_phase_as_query(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When active run has task+phase, those form the query tokens."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: implement\ntask: fix-scoring\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        captured_calls: list[dict[str, Any]] = []

        def _fake_recall(
            trw_dir_arg: Any,
            *,
            query: str = "*",
            tags: Any = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            captured_calls.append({"query": query, "tags": tags, "min_impact": min_impact})
            return [{"id": "L-x1", "summary": "Scoring fix tip", "impact": 0.9, "tags": ["gotcha"], "status": "active"}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        assert result["auto_recall_count"] == 1
        # Verify the auto-recall call (Step 6) used task+phase as query string
        # Step 1 (main recall) calls recall_learnings with query="*", min_impact=0.7
        # Step 6 (auto-recall) calls with query="fix-scoring implement", min_impact=0.5
        auto_recall_call = None
        for call in captured_calls:
            if call["min_impact"] == 0.5:  # Step 6 uses min_impact=0.5
                auto_recall_call = call
                break
        assert auto_recall_call is not None
        assert "fix-scoring" in auto_recall_call["query"]
        assert "implement" in auto_recall_call["query"]
        # Phase tags for implement should be passed
        assert auto_recall_call.get("tags") == ["gotcha", "testing", "pattern"]

    def test_uses_wildcard_when_no_task_context(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no active run, query tokens default to empty (wildcard)."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        captured_calls: list[dict[str, Any]] = []

        def _fake_recall(
            trw_dir_arg: Any,
            *,
            query: str = "*",
            tags: Any = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            captured_calls.append({"query": query, "tags": tags, "min_impact": min_impact})
            return [{"id": "L-y1", "summary": "General tip", "impact": 0.8, "tags": ["pattern"], "status": "active"}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        # Find the auto-recall call (Step 6) -- it has min_impact=0.5
        auto_recall_call = None
        for call in captured_calls:
            if call.get("min_impact") == 0.5:
                auto_recall_call = call
                break
        assert auto_recall_call is not None
        # No task context -> wildcard query
        assert auto_recall_call["query"] == "*"
        # No tags for wildcard (no phase)
        assert auto_recall_call.get("tags") is None


class TestAutoRecallFailOpen:
    """Auto-recall errors must not crash trw_session_start."""

    def test_error_failopen(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When search_entries raises in auto-recall step, session_start still succeeds."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        call_count = {"n": 0}

        def _failing_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            call_count["n"] += 1
            if call_count["n"] > 1:
                # Auto-recall (Step 6) fails
                raise RuntimeError("search engine down")
            # Step 1 succeeds
            return []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_failing_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        # Auto-recall failure must not appear in errors or block result
        assert "auto_recalled" not in result
        assert result["success"] is True
        assert "timestamp" in result

    def test_error_failopen_with_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-recall error with active run does not affect run status."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: research\ntask_name: test\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        call_count = {"n": 0}

        def _failing_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise Exception("recall boom")
            return []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_failing_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        # Run status should still be present
        assert "run" in result
        assert result["run"]["status"] == "active"
        assert result["run"]["phase"] == "research"
        # Auto-recall failure is silent
        assert "auto_recalled" not in result


class TestAutoRecallMaxResults:
    """Auto-recall respects max_results config."""

    def test_respects_max_results(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_recall_max_results limits returned entries."""
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig(auto_recall_max_results=2)
        monkeypatch.setattr("trw_mcp.tools.ceremony.get_config", lambda: mock_cfg)

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        mock_entries = [
            {"id": f"L-{i}", "summary": f"Learning {i}", "impact": 0.8 - i * 0.05, "tags": [], "status": "active"}
            for i in range(5)
        ]

        def _fake_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return mock_entries

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        # With max_results=2, only top 2 should appear
        assert result["auto_recall_count"] == 2
        assert len(result["auto_recalled"]) == 2


class TestAutoRecallConfigFields:
    """Config fields for auto-recall exist and have correct defaults."""

    def test_auto_recall_enabled_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_enabled is True

    def test_auto_recall_max_results_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_max_results == 3

    def test_auto_recall_max_tokens_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_max_tokens == 100

    def test_auto_recall_min_score_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_min_score == 0.7

    def test_auto_recall_enabled_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_ENABLED env var can disable auto-recall."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_ENABLED", "false")
        config = TRWConfig()
        assert config.auto_recall_enabled is False

    def test_auto_recall_max_results_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MAX_RESULTS env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MAX_RESULTS", "10")
        config = TRWConfig()
        assert config.auto_recall_max_results == 10

    def test_auto_recall_max_tokens_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MAX_TOKENS env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MAX_TOKENS", "42")
        config = TRWConfig()
        assert config.auto_recall_max_tokens == 42

    def test_auto_recall_min_score_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MIN_SCORE env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MIN_SCORE", "0.9")
        config = TRWConfig()
        assert config.auto_recall_min_score == 0.9
