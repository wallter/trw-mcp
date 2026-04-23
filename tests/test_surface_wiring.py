"""Tests for surface event wiring in recall flow (PRD-CORE-103-FR01, Sprint 83 Task 5).

Verifies that ``execute_recall()`` calls ``log_surface_event()`` for each
returned learning when the query is NOT compact/wildcard, and that surface
logging failures never break the recall flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import get_config


def _make_entry(entry_id: str = "L-001", **kwargs: object) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": "test learning",
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
        "status": "active",
        "tags": [],
        **kwargs,
    }


def _noop_rank(
    matches: list[dict[str, object]],
    query_tokens: list[str],
    lambda_weight: float,
    assertion_penalties: dict[str, float] | None = None,
    *,
    context: object | None = None,
) -> list[dict[str, object]]:
    """Passthrough rank function for testing."""
    return list(matches)


def _run_execute_recall(
    tmp_path: Path,
    *,
    query: str = "auth middleware",
    entries: list[dict[str, object]] | None = None,
    compact: bool | None = None,
    extra_patches: dict[str, Any] | None = None,
) -> tuple[Any, MagicMock]:
    """Helper: run execute_recall with standard mocks and return (result, mock_log_surface).

    The returned mock_log_surface is the patched ``log_surface_event`` callable
    so callers can assert on its calls.
    """
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    config = get_config()

    if entries is None:
        entries = [_make_entry("L-a1"), _make_entry("L-a2")]

    mock_log = MagicMock()
    patch_stack = [
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
        patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
        patch("trw_mcp.state.recall_search.collect_context", return_value={}),
        patch("trw_mcp.tools._recall_impl._track_recall"),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
        patch("trw_mcp.tools._recall_impl.log_surface_event", mock_log),
    ]
    if extra_patches:
        for k, v in extra_patches.items():
            patch_stack.append(patch(k, v))

    for p in patch_stack:
        p.start()
    try:
        result = execute_recall(
            query=query,
            trw_dir=trw_dir,
            config=config,
            compact=compact,
            _rank_by_utility=_noop_rank,
        )
    finally:
        for p in patch_stack:
            p.stop()

    return result, mock_log


# ---------------------------------------------------------------------------
# Tests: surface logging on normal recall
# ---------------------------------------------------------------------------


class TestRecallLogsSurfaceEvent:
    """Verify log_surface_event is called for each surfaced learning."""

    def test_recall_logs_surface_event_for_each_learning(self, tmp_path: Path) -> None:
        """trw_recall logs a surface event for each returned learning."""
        entries = [_make_entry("L-a1"), _make_entry("L-a2")]
        result, mock_log = _run_execute_recall(tmp_path, entries=entries)

        # Should have logged 2 events (one per learning)
        assert mock_log.call_count == 2

        # Verify learning IDs passed correctly
        logged_ids = [c.kwargs["learning_id"] for c in mock_log.call_args_list]
        assert "L-a1" in logged_ids
        assert "L-a2" in logged_ids

    def test_surface_type_is_recall(self, tmp_path: Path) -> None:
        """Surface events from trw_recall have surface_type='recall'."""
        entries = [_make_entry("L-x1")]
        _, mock_log = _run_execute_recall(tmp_path, entries=entries)

        assert mock_log.call_count == 1
        assert mock_log.call_args_list[0].kwargs["surface_type"] == "recall"

    def test_surface_event_includes_trw_dir(self, tmp_path: Path) -> None:
        """Surface events pass trw_dir as the first positional argument."""
        entries = [_make_entry("L-x1")]
        _, mock_log = _run_execute_recall(tmp_path, entries=entries)

        assert mock_log.call_count == 1
        # First positional arg is trw_dir
        assert mock_log.call_args_list[0].args[0] == tmp_path / ".trw"

    def test_surface_event_caps_files_context(self, tmp_path: Path) -> None:
        """files_context is capped to 5 entries to prevent JSONL bloat."""
        entries = [_make_entry("L-x1")]
        _, mock_log = _run_execute_recall(tmp_path, entries=entries)

        assert mock_log.call_count == 1
        files = mock_log.call_args_list[0].kwargs.get("files_context", [])
        assert len(files) <= 5

    def test_empty_results_no_surface_events(self, tmp_path: Path) -> None:
        """No surface events logged when recall returns zero learnings."""
        _, mock_log = _run_execute_recall(tmp_path, entries=[])

        assert mock_log.call_count == 0

    def test_entries_without_id_skipped(self, tmp_path: Path) -> None:
        """Entries missing an 'id' field are skipped for surface logging."""
        entries = [
            {"summary": "no id", "impact": 0.5, "status": "active", "tags": []},
            _make_entry("L-has-id"),
        ]
        _, mock_log = _run_execute_recall(tmp_path, entries=entries)

        assert mock_log.call_count == 1
        assert mock_log.call_args_list[0].kwargs["learning_id"] == "L-has-id"


# ---------------------------------------------------------------------------
# Tests: compact/wildcard queries NOT logged
# ---------------------------------------------------------------------------


class TestCompactQueriesNotLogged:
    """Compact and wildcard queries should not generate surface events."""

    def test_compact_queries_not_logged(self, tmp_path: Path) -> None:
        """Compact queries don't generate surface events."""
        entries = [_make_entry("L-c1"), _make_entry("L-c2")]
        _, mock_log = _run_execute_recall(tmp_path, entries=entries, compact=True)

        assert mock_log.call_count == 0

    def test_wildcard_queries_not_logged(self, tmp_path: Path) -> None:
        """Wildcard ('*') queries auto-enable compact and skip surface logging."""
        entries = [_make_entry("L-w1")]
        _, mock_log = _run_execute_recall(tmp_path, query="*", entries=entries)

        # Wildcard auto-enables compact, so no surface events
        assert mock_log.call_count == 0

    def test_empty_query_not_logged(self, tmp_path: Path) -> None:
        """Empty query is treated as wildcard and skips surface logging."""
        entries = [_make_entry("L-e1")]
        _, mock_log = _run_execute_recall(tmp_path, query="", entries=entries)

        assert mock_log.call_count == 0


# ---------------------------------------------------------------------------
# Tests: fail-open behavior
# ---------------------------------------------------------------------------


class TestSurfaceLoggingFailOpen:
    """Surface logging failures must never break recall."""

    def test_surface_logging_failure_does_not_break_recall(self, tmp_path: Path) -> None:
        """If log_surface_event raises, recall still returns results."""
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = get_config()
        entries = [_make_entry("L-f1")]

        mock_log = MagicMock(side_effect=RuntimeError("disk full"))

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
            patch("trw_mcp.tools._recall_impl.log_surface_event", mock_log),
        ):
            result = execute_recall(
                query="auth",
                trw_dir=trw_dir,
                config=config,
                _rank_by_utility=_noop_rank,
            )

        # Recall should still succeed
        assert "learnings" in result
        assert len(result["learnings"]) == 1

    def test_surface_import_failure_does_not_break_recall(self, tmp_path: Path) -> None:
        """If log_surface_event import fails at runtime, recall still works.

        This tests the try/except around the deferred import inside
        execute_recall — NOT the module-level import we added.
        """
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = get_config()
        entries = [_make_entry("L-i1")]

        # Make the function raise on call (simulating import or runtime failure)
        mock_log = MagicMock(side_effect=ImportError("module not found"))

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
            patch("trw_mcp.tools._recall_impl.log_surface_event", mock_log),
        ):
            result = execute_recall(
                query="middleware",
                trw_dir=trw_dir,
                config=config,
                _rank_by_utility=_noop_rank,
            )

        assert "learnings" in result
        assert len(result["learnings"]) == 1

    def test_recall_context_import_failure_fails_open(self, tmp_path: Path) -> None:
        """If contextual recall wiring is unavailable, execute_recall still succeeds."""
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = get_config()
        entries = [_make_entry("L-i2")]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
            patch(
                "trw_mcp.state.recall_context.build_recall_context",
                side_effect=ImportError("context module missing"),
            ),
        ):
            result = execute_recall(
                query="middleware",
                trw_dir=trw_dir,
                config=config,
                _rank_by_utility=_noop_rank,
            )

        assert "learnings" in result
        assert len(result["learnings"]) == 1


# ---------------------------------------------------------------------------
# Tests: phase detection wiring
# ---------------------------------------------------------------------------


class TestSurfacePhaseDetection:
    """Verify phase detection flows into surface events."""

    def test_phase_from_detect_current_phase(self, tmp_path: Path) -> None:
        """Surface events include the current phase when detectable."""
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = get_config()
        entries = [_make_entry("L-p1")]

        mock_log = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
            patch("trw_mcp.tools._recall_impl.log_surface_event", mock_log),
            patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value="IMPLEMENT"),
        ):
            execute_recall(
                query="auth",
                trw_dir=trw_dir,
                config=config,
                _rank_by_utility=_noop_rank,
            )

        assert mock_log.call_count == 1
        assert mock_log.call_args_list[0].kwargs["phase"] == "IMPLEMENT"

    def test_phase_empty_when_undetectable(self, tmp_path: Path) -> None:
        """Surface events use empty string when phase detection fails."""
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = get_config()
        entries = [_make_entry("L-p2")]

        mock_log = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=list(entries)),
            patch("trw_mcp.tools._recall_impl.log_surface_event", mock_log),
            patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        ):
            execute_recall(
                query="auth",
                trw_dir=trw_dir,
                config=config,
                _rank_by_utility=_noop_rank,
            )

        assert mock_log.call_count == 1
        assert mock_log.call_args_list[0].kwargs["phase"] == ""
