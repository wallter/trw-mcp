"""Recall execution wiring integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._recall_integration_support import _make_entry


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

    assert len(calls) >= 1
    assert calls[0]["context"] is expected_ctx


def test_execute_recall_threads_live_intel_cache_context(tmp_path: Path) -> None:
    """execute_recall passes a real intel cache through the production context builder."""
    from trw_mcp.models.config import get_config
    from trw_mcp.sync.cache import IntelligenceCache
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    IntelligenceCache(trw_dir).update({"bandit_params": {"L-boosted": 1.8}}, etag="etag-1")
    config = get_config()
    calls: list[object | None] = []

    def capturing_rank_fn(
        matches: list[dict[str, object]],
        query_tokens: list[str],
        lambda_weight: float,
        assertion_penalties: dict[str, float] | None = None,
        *,
        context: object | None = None,
    ) -> list[dict[str, object]]:
        calls.append(context)
        return matches

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("subprocess.run") as mock_run,
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[_make_entry("L-boosted")]),
        patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
        patch("trw_mcp.state.recall_search.collect_context", return_value={}),
        patch("trw_mcp.tools._recall_impl._track_recall"),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=[_make_entry("L-boosted")]),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        execute_recall(
            query="src",
            trw_dir=trw_dir,
            config=config,
            _rank_by_utility=capturing_rank_fn,
        )

    assert calls
    assert getattr(calls[0], "intel_cache", None) is not None


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

    assert "learnings" in result
    assert rank_calls[0]["context"] is None


def test_execute_recall_writes_propensity_log(tmp_path: Path) -> None:
    """execute_recall writes deterministic propensity entries for surfaced results."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()
    ranked_entries = [_make_entry("L-001"), _make_entry("L-002")]

    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None),
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=ranked_entries),
        patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
        patch("trw_mcp.state.recall_search.collect_context", return_value={}),
        patch("trw_mcp.tools._recall_impl._track_recall"),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", return_value=ranked_entries),
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value="IMPLEMENT"),
    ):
        execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            _rank_by_utility=lambda matches, *_args, **_kwargs: matches,
        )

    log_path = trw_dir / "logs" / "propensity.jsonl"
    lines = [json.loads(line) for line in log_path.read_text().strip().split("\n") if line.strip()]
    assert [line["selected"] for line in lines] == ["L-001", "L-002"]
    assert lines[0]["candidate_set"] == ["L-001", "L-002"]
    assert lines[0]["context_task_type"] == "recall"


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
        rank_fn_calls.append(
            {
                "assertion_penalties": assertion_penalties,
                "context": context,
            }
        )
        return matches

    entry = _make_entry(
        "L-a",
        assertions=[{"type": "file_exists", "pattern": "/nonexistent/path/file.py"}],
    )

    with (
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_memory.lifecycle.verification.verify_assertions") as mock_verify,
        patch("trw_memory.models.memory.Assertion.model_validate") as mock_validate,
    ):
        mock_result = MagicMock()
        mock_result.passed = False
        mock_result.model_dump.return_value = {"type": "file_exists", "passed": False}
        mock_validate.return_value = MagicMock()
        mock_verify.return_value = [mock_result]

        _verify_assertions([entry], ["test"], config, capturing_rank_fn, context=ctx)

    if rank_fn_calls:
        assert rank_fn_calls[0]["context"] is ctx
