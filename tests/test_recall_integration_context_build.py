"""RecallContext construction integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

def test_recall_context_construction(tmp_path: Path) -> None:
    """build_recall_context returns valid RecallContext with phase when detect_current_phase returns a phase."""
    from trw_mcp.scoring._recall import RecallContext
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

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

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ctx = build_recall_context(trw_dir, "src")

    assert ctx is None


def test_build_recall_context_returns_cache_only_context(tmp_path: Path) -> None:
    """A populated intel cache keeps recall-context wiring alive without phase/domain hints."""
    from trw_mcp.sync.cache import IntelligenceCache
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    IntelligenceCache(trw_dir).update({"bandit_params": {"L-boosted": 1.7}}, etag="etag-1")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ctx = build_recall_context(trw_dir, "src")

    assert ctx is not None
    assert ctx.intel_cache is not None


def test_recall_context_git_failure_graceful(tmp_path: Path) -> None:
    """build_recall_context handles git failure gracefully."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with patch("subprocess.run", side_effect=OSError("git not found")):
        ctx = build_recall_context(trw_dir, "auth middleware")

    if ctx is not None:
        assert "auth" in ctx.active_domains or "middleware" in ctx.active_domains


def test_build_recall_context_no_run_files(tmp_path: Path) -> None:
    """build_recall_context handles missing run files gracefully."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="src/auth/login.py\n")
        ctx = build_recall_context(trw_dir, "login")

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
        ctx = build_recall_context(trw_dir, "auth")

    if ctx is not None:
        assert ctx.modified_files == []


def test_build_recall_context_threads_prd_knowledge_ids(tmp_path: Path) -> None:
    """build_recall_context reads prd_knowledge_ids from knowledge_requirements.yaml."""
    from trw_mcp.tools._recall_impl import build_recall_context

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    run_dir = tmp_path / "runs" / "test-task" / "run-001"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "knowledge_requirements.yaml").write_text("learning_ids:\n  - L-abc01\n  - L-def02\n")

    # PRD-FIX-083: build_recall_context now uses get_pinned_run() in the
    # no-ctx fallback path (was find_active_run()). Patch the new resolver.
    with (
        patch("subprocess.run") as mock_run,
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value="IMPLEMENT"),
        patch("trw_mcp.state._paths.get_pinned_run", return_value=run_dir),
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
