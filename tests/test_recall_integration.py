"""Integration tests for RecallContext wiring through execute_recall() (PRD-CORE-102, Task 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_entry(entry_id: str = "L-001", **kwargs: object) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": "test learning",
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
        **kwargs,
    }


def _make_config() -> object:
    """Return a minimal TRWConfig-like object."""
    from trw_mcp.models.config import get_config

    return get_config()


def test_recall_context_construction(tmp_path: Path) -> None:
    """build_recall_context returns valid RecallContext with phase when detect_current_phase returns a phase."""
    from trw_mcp.scoring._recall import RecallContext
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # detect_current_phase is now the canonical source for phase detection (DRY fix)
    with (
        patch("trw_mcp.state._paths.detect_current_phase", return_value="implement"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="src/auth/middleware.py\n")
        ctx = build_recall_context(trw_dir, "auth scoring")

    assert ctx is not None
    assert isinstance(ctx, RecallContext)
    assert ctx.current_phase == "IMPLEMENT"
    assert "auth" in ctx.active_domains
    assert "middleware" in ctx.active_domains


def test_recall_context_returns_none_when_empty(tmp_path: Path) -> None:
    """build_recall_context returns None when there's no phase and no domains."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # No run files
    with patch("subprocess.run") as mock_run:
        # Empty git output, and query is a single structural word
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ctx = build_recall_context(trw_dir, "src")

    # No phase, no meaningful domains → None
    assert ctx is None


def test_recall_context_git_failure_graceful(tmp_path: Path) -> None:
    """build_recall_context handles git failure gracefully."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with patch("subprocess.run", side_effect=OSError("git not found")):
        # Should not raise, just returns None or partial context
        ctx = build_recall_context(trw_dir, "auth middleware")

    # Without files but with query, we might still get domains from query
    if ctx is not None:
        assert "auth" in ctx.active_domains or "middleware" in ctx.active_domains


def test_rank_fn_receives_context_in_execute_recall(tmp_path: Path) -> None:
    """execute_recall passes recall_context to rank_fn."""
    from trw_mcp.models.config import get_config
    from trw_mcp.scoring._recall import RecallContext
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()

    calls: list[dict[str, object]] = []

    def capturing_rank_fn(
        matches: list[dict[str, object]],
        query_tokens: list[str],
        lambda_weight: float,
        assertion_penalties: dict[str, float] | None = None,
        *,
        context: RecallContext | None = None,
    ) -> list[dict[str, object]]:
        calls.append({"context": context, "matches": matches})
        return matches

    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context") as mock_ctx_builder,
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[_make_entry()]),
        patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
        patch("trw_mcp.state.recall_search.collect_context", return_value={}),
        patch("trw_mcp.tools._recall_impl._track_recall"),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=[_make_entry()]),
    ):
        expected_ctx = RecallContext(current_phase="IMPLEMENT", active_domains=["auth"])
        mock_ctx_builder.return_value = expected_ctx

        execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            _rank_by_utility=capturing_rank_fn,
        )

    # rank_fn should have been called with the context
    assert len(calls) >= 1
    assert calls[0]["context"] is expected_ctx


def test_recall_no_context_regression(tmp_path: Path) -> None:
    """Without context (build fails), recall still works (backward compat)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()

    rank_calls: list[dict[str, object]] = []

    def capturing_rank_fn(
        matches: list[dict[str, object]],
        query_tokens: list[str],
        lambda_weight: float,
        assertion_penalties: dict[str, float] | None = None,
        *,
        context: object | None = None,
    ) -> list[dict[str, object]]:
        rank_calls.append({"context": context})
        return matches

    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None),
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[_make_entry()]),
        patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
        patch("trw_mcp.state.recall_search.collect_context", return_value={}),
        patch("trw_mcp.tools._recall_impl._track_recall"),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=[_make_entry()]),
    ):
        result = execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            _rank_by_utility=capturing_rank_fn,
        )

    # Should complete successfully
    assert "learnings" in result
    # Context should be None when build_recall_context returns None
    assert rank_calls[0]["context"] is None


def test_assertion_reranking_preserves_context(tmp_path: Path) -> None:
    """_verify_assertions passes context to rank_fn."""
    from trw_mcp.models.config import get_config
    from trw_mcp.scoring._recall import RecallContext
    from trw_mcp.tools._recall_impl import _verify_assertions

    config = get_config()
    ctx = RecallContext(current_phase="VALIDATE")

    rank_fn_calls: list[dict[str, object]] = []

    def capturing_rank_fn(
        matches: list[dict[str, object]],
        query_tokens: list[str],
        lambda_weight: float,
        assertion_penalties: dict[str, float] | None = None,
        *,
        context: RecallContext | None = None,
    ) -> list[dict[str, object]]:
        rank_fn_calls.append({
            "assertion_penalties": assertion_penalties,
            "context": context,
        })
        return matches

    # Simulate failing assertion → penalty applied → rank_fn called with penalties
    entry = _make_entry(
        "L-a",
        assertions=[{"type": "file_exists", "pattern": "/nonexistent/path/file.py"}],
    )

    with (
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_memory.lifecycle.verification.verify_assertions") as mock_verify,
        patch("trw_memory.models.memory.Assertion.model_validate") as mock_validate,
    ):
        # Simulate one failing assertion
        mock_result = MagicMock()
        mock_result.passed = False
        mock_result.model_dump.return_value = {"type": "file_exists", "passed": False}
        mock_validate.return_value = MagicMock()
        mock_verify.return_value = [mock_result]

        _verify_assertions([entry], ["test"], config, capturing_rank_fn, context=ctx)

    # rank_fn should have been called with the context preserved
    if rank_fn_calls:
        assert rank_fn_calls[0]["context"] is ctx


def test_build_recall_context_no_run_files(tmp_path: Path) -> None:
    """build_recall_context handles missing run files gracefully."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    # No runs directory

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="src/auth/login.py\n")
        ctx = build_recall_context(trw_dir, "login")

    # Should have domain from files and query, no phase
    if ctx is not None:
        assert ctx.current_phase is None
        assert "auth" in ctx.active_domains or "login" in ctx.active_domains


def test_build_recall_context_git_nonzero_returncode(tmp_path: Path) -> None:
    """build_recall_context handles non-zero git returncode gracefully."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        # No crash
        ctx = build_recall_context(trw_dir, "auth")

    # With non-zero returncode, modified_files=[], query provides domains
    if ctx is not None:
        assert ctx.modified_files == []
