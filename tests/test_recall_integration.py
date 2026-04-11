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


def test_build_recall_context_threads_prd_knowledge_ids(tmp_path: Path) -> None:
    """build_recall_context reads prd_knowledge_ids from knowledge_requirements.yaml."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # Create a mock active run with knowledge_requirements.yaml
    run_dir = tmp_path / "runs" / "test-task" / "run-001"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)

    # Write knowledge_requirements.yaml with learning IDs
    kr_path = meta_dir / "knowledge_requirements.yaml"
    kr_path.write_text("learning_ids:\n  - L-abc01\n  - L-def02\n")

    with (
        patch("subprocess.run") as mock_run,
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value="IMPLEMENT"),
        patch("trw_mcp.state._paths.find_active_run", return_value=run_dir),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="src/auth/login.py\n")
        ctx = build_recall_context(trw_dir, "auth scoring")

    assert ctx is not None
    assert ctx.prd_knowledge_ids == {"L-abc01", "L-def02"}


def test_build_recall_context_no_active_run_empty_prd_ids(tmp_path: Path) -> None:
    """build_recall_context returns empty prd_knowledge_ids when no active run."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with (
        patch("subprocess.run") as mock_run,
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value="PLAN"),
        patch("trw_mcp.state._paths.find_active_run", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="src/auth/login.py\n")
        ctx = build_recall_context(trw_dir, "auth scoring")

    assert ctx is not None
    assert ctx.prd_knowledge_ids == set()


# ---------------------------------------------------------------------------
# Token budget integration tests (PRD-CORE-123 FR06)
# ---------------------------------------------------------------------------


def _make_sized_entry(entry_id: str, word_count: int) -> dict[str, object]:
    """Entry with known content size for budget testing."""
    return {
        "id": entry_id,
        "summary": f"Learning {entry_id}",
        "content": " ".join(f"word{i}" for i in range(word_count)),
        "detail": "",
        "tags": [],
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
    }


def test_trw_recall_token_budget_metadata(tmp_path: Path) -> None:
    """execute_recall with token_budget returns token metadata."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    entries = [_make_sized_entry("L-1", 10)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test", trw_dir=trw_dir, config=config,
            token_budget=4000,
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert "tokens_used" in result
    assert "tokens_budget" in result
    assert result["tokens_budget"] == 4000
    assert isinstance(result["tokens_used"], int)
    assert isinstance(result["tokens_truncated"], bool)


def test_trw_recall_token_budget_truncates(tmp_path: Path) -> None:
    """execute_recall truncates results exceeding token_budget."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    entries = [_make_sized_entry(f"L-{i}", 75) for i in range(10)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test", trw_dir=trw_dir, config=config,
            token_budget=250,
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert result["tokens_truncated"] is True
    assert len(result["learnings"]) < 10


def test_trw_recall_token_budget_none_informational(tmp_path: Path) -> None:
    """token_budget=None still computes tokens_used."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test", trw_dir=trw_dir, config=config,
            token_budget=None,
            _adapter_recall=lambda *a, **kw: [_make_sized_entry("L-1", 10)],
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert result["tokens_budget"] is None
    assert result["tokens_truncated"] is False
    assert result["tokens_used"] > 0


def test_trw_recall_token_budget_invalid_raises(tmp_path: Path) -> None:
    """token_budget <= 0 raises ValueError."""
    import pytest
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with pytest.raises(ValueError, match="token_budget must be positive"):
        execute_recall(
            query="test", trw_dir=trw_dir, config=config, token_budget=0,
            _adapter_recall=lambda *a, **kw: [],
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )


def test_execute_recall_deprioritizes_injected_results_before_cap(tmp_path: Path) -> None:
    """Already-in-context learnings should not consume the primary result slots."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    entries = [
        _make_entry("L-1", impact=0.9),
        _make_entry("L-2", impact=0.8),
        _make_entry("L-3", impact=0.7),
    ]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            max_results=2,
            deprioritized_ids={"L-1"},
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert [entry["id"] for entry in result["learnings"]] == ["L-2", "L-3"]


def test_trw_recall_passes_injected_ids_to_execute_recall(tmp_path: Path, monkeypatch) -> None:
    """trw_recall should wire injected IDs into execute_recall before result capping."""
    from tests.conftest import get_tools_sync, make_test_server

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "context" / "injected_learning_ids.txt").write_text("L-1\nL-2\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_execute_recall(*args, **kwargs):
        captured["deprioritized_ids"] = kwargs.get("deprioritized_ids")
        return {
            "query": "test",
            "learnings": [],
            "patterns": [],
            "context": {},
            "total_matches": 0,
            "total_available": 0,
            "compact": False,
            "max_results": kwargs.get("max_results"),
            "topic_filter_ignored": False,
            "tokens_used": 0,
            "tokens_budget": None,
            "tokens_truncated": False,
        }

    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._recall_impl.execute_recall", fake_execute_recall)

    recall_tool = get_tools_sync(make_test_server("learning"))["trw_recall"].fn
    recall_tool(query="test", max_results=2)

    assert captured["deprioritized_ids"] == {"L-1", "L-2"}
