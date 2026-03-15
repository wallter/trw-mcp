"""Tests for REVIEW.md auto-injection (PRD-CORE-084 FR08).

Covers:
  - generate_review_md() generates correct REVIEW.md content
  - _sanitize_summary() strips markdown links, HTML tags
  - Atomic write via temp+rename
  - Fail-open: errors don't propagate
  - Integration with execute_claude_md_sync
  - Learning selection: tags, impact, status, ordering, cap
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Unit tests: _sanitize_summary
# ---------------------------------------------------------------------------


class TestSanitizeSummary:
    """Unit tests for _sanitize_summary helper."""

    def test_strips_markdown_links(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("See [docs](https://example.com) for details")
        assert result == "See docs for details"

    def test_strips_multiple_markdown_links(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("[foo](http://a.com) and [bar](http://b.com)")
        assert result == "foo and bar"

    def test_strips_html_tags(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("Use <code>foo</code> carefully")
        assert result == "Use foo carefully"

    def test_strips_self_closing_html(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("Line break<br/>here")
        assert result == "Line breakhere"

    def test_preserves_plain_text(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("No special formatting here")
        assert result == "No special formatting here"

    def test_strips_whitespace(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("  leading and trailing  ")
        assert result == "leading and trailing"

    def test_empty_string(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("")
        assert result == ""

    def test_combined_markdown_and_html(self) -> None:
        from trw_mcp.state.claude_md._sync import _sanitize_summary

        result = _sanitize_summary("[link](url) and <b>bold</b>")
        assert result == "link and bold"


# ---------------------------------------------------------------------------
# Unit tests: generate_review_md
# ---------------------------------------------------------------------------


class TestGenerateReviewMd:
    """Tests for generate_review_md function."""

    def test_generates_review_md_with_no_learnings(self, tmp_path: Path) -> None:
        """When no learnings qualify, REVIEW.md has the no-learnings comment."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=[],
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        assert result["status"] == "generated"
        assert result["rules_count"] == 0

        review_path = Path(str(result["path"]))
        assert review_path.exists()
        content = review_path.read_text(encoding="utf-8")
        assert "# REVIEW.md" in content
        assert "TRW:AUTO-GENERATED" in content
        assert "No qualifying learnings" in content
        assert "## Always check" in content
        assert "## Skip" in content

    def test_generates_review_md_with_learnings(self, tmp_path: Path) -> None:
        """Qualifying learnings appear as Flag entries."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        mock_learnings = [
            {
                "id": "L-001",
                "summary": "Always check return types",
                "impact": 0.8,
                "status": "active",
                "tags": ["gotcha", "type-safety"],
            },
            {
                "id": "L-002",
                "summary": "Validate [inputs](http://docs.example.com) before use",
                "impact": 0.9,
                "status": "active",
                "tags": ["security"],
            },
        ]

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=mock_learnings,
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        assert result["status"] == "generated"
        assert result["rules_count"] == 2

        review_path = Path(str(result["path"]))
        content = review_path.read_text(encoding="utf-8")

        # Check learning entries are present
        assert "- Flag: Always check return types (L-001)" in content
        # Markdown link should be stripped
        assert "- Flag: Validate inputs before use (L-002)" in content
        assert "http://docs.example.com" not in content

    def test_caps_at_20_learnings(self, tmp_path: Path) -> None:
        """At most 20 learnings are included."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        # Create 25 mock learnings
        mock_learnings = [
            {
                "id": f"L-{i:03d}",
                "summary": f"Learning number {i}",
                "impact": 0.8,
                "status": "active",
                "tags": ["gotcha"],
            }
            for i in range(25)
        ]

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=mock_learnings,
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        assert result["rules_count"] == 20

        content = Path(str(result["path"])).read_text(encoding="utf-8")
        flag_count = content.count("- Flag:")
        assert flag_count == 20

    def test_review_md_written_at_repo_root(self, tmp_path: Path) -> None:
        """REVIEW.md is written at the repo root, not in .trw."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=[],
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        expected_path = tmp_path / "REVIEW.md"
        assert Path(str(result["path"])) == expected_path
        assert expected_path.exists()

    def test_atomic_write_no_partial_file_on_error(self, tmp_path: Path) -> None:
        """If writing fails mid-way, no partial REVIEW.md is left behind."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        # Make the repo_root read-only to force a write error
        review_path = tmp_path / "REVIEW.md"
        assert not review_path.exists()

        with (
            patch(
                "trw_mcp.state.claude_md._sync.recall_learnings",
                return_value=[],
            ),
            patch(
                "trw_mcp.state.claude_md._sync.tempfile.mkstemp",
                side_effect=OSError("disk full"),
            ),
        ):
            # Should not raise (fail-open)
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        assert result["status"] == "failed"
        assert not review_path.exists()

    def test_repo_root_auto_detection(self, tmp_path: Path) -> None:
        """When repo_root is None, _get_repo_root is called."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with (
            patch(
                "trw_mcp.state.claude_md._sync.recall_learnings",
                return_value=[],
            ),
            patch(
                "trw_mcp.state.claude_md._sync._get_repo_root",
                return_value=tmp_path,
            ),
        ):
            result = generate_review_md(trw_dir, repo_root=None)

        assert result["status"] == "generated"
        assert (tmp_path / "REVIEW.md").exists()

    def test_repo_root_none_and_no_git(self, tmp_path: Path) -> None:
        """When repo_root is None and git detection fails, return error."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with (
            patch(
                "trw_mcp.state.claude_md._sync.recall_learnings",
                return_value=[],
            ),
            patch(
                "trw_mcp.state.claude_md._sync._get_repo_root",
                return_value=None,
            ),
        ):
            result = generate_review_md(trw_dir, repo_root=None)

        assert result["status"] == "failed"

    def test_review_md_contains_skip_section(self, tmp_path: Path) -> None:
        """REVIEW.md contains the Skip section with expected patterns."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=[],
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        content = Path(str(result["path"])).read_text(encoding="utf-8")
        assert "## Skip" in content
        assert "docs/sprint-*/runs/**" in content
        assert ".trw/**" in content
        assert "**/scratch/**" in content

    def test_review_md_contains_always_check_section(self, tmp_path: Path) -> None:
        """REVIEW.md contains the Always check section."""
        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.claude_md._sync.recall_learnings",
            return_value=[],
        ):
            result = generate_review_md(trw_dir, repo_root=tmp_path)

        content = Path(str(result["path"])).read_text(encoding="utf-8")
        assert "## Always check" in content
        assert "public functions without corresponding tests" in content
        assert "`Any` type annotations" in content
        assert "type: ignore" in content
        assert "input validation" in content
        assert "orphan detection" in content

    def test_fr08_below_threshold_excluded(self, tmp_path: Path) -> None:
        """generate_review_md passes min_impact=0.7 so below-threshold learnings are filtered.

        The filtering happens inside recall_learnings (at the query layer). We verify
        that generate_review_md calls recall_learnings with the correct min_impact=0.7
        so the system would exclude entries with impact < 0.7.
        """
        from unittest.mock import MagicMock

        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        repo_root = tmp_path

        mock_recall = MagicMock(return_value=[])  # returns empty — simulates threshold filtering
        with patch("trw_mcp.state.claude_md._sync.recall_learnings", mock_recall):
            result = generate_review_md(trw_dir, repo_root=repo_root)

        # Verify the threshold argument is passed correctly
        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("min_impact") == 0.7

        # And when nothing qualifies, L-low should not appear
        review_path = repo_root / "REVIEW.md"
        if review_path.exists():
            content = review_path.read_text()
            assert "L-low" not in content

    def test_fr08_retired_learning_excluded(self, tmp_path: Path) -> None:
        """generate_review_md passes status='active' so retired learnings are filtered.

        The filtering happens inside recall_learnings (at the query layer). We verify
        that generate_review_md calls recall_learnings with status='active' so retired
        entries are excluded before they reach REVIEW.md.
        """
        from unittest.mock import MagicMock

        from trw_mcp.state.claude_md._sync import generate_review_md

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        repo_root = tmp_path

        mock_recall = MagicMock(return_value=[])  # returns empty — simulates status filtering
        with patch("trw_mcp.state.claude_md._sync.recall_learnings", mock_recall):
            result = generate_review_md(trw_dir, repo_root=repo_root)

        # Verify the status argument is passed correctly
        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("status") == "active"

        # And when nothing qualifies, L-retired should not appear
        review_path = repo_root / "REVIEW.md"
        if review_path.exists():
            content = review_path.read_text()
            assert "L-retired" not in content


# ---------------------------------------------------------------------------
# Integration: generate_review_md called from execute_claude_md_sync
# ---------------------------------------------------------------------------


class TestReviewMdIntegration:
    """REVIEW.md generation is called from execute_claude_md_sync."""

    def _make_sync_args(self, tmp_path: Path) -> dict[str, object]:
        """Build minimal args for execute_claude_md_sync."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "reflections").mkdir(exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)
        (trw_dir / "patterns").mkdir(exist_ok=True)

        config = TRWConfig(trw_dir=str(trw_dir))
        reader = FileStateReader()
        writer = FileStateWriter()
        llm = MagicMock()
        llm.available = False

        return {
            "scope": "root",
            "target_dir": None,
            "config": config,
            "reader": reader,
            "writer": writer,
            "llm": llm,
        }

    def test_sync_includes_review_md_in_result(self, tmp_path: Path) -> None:
        """execute_claude_md_sync result includes review_md key."""
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = self._make_sync_args(tmp_path)

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch("trw_mcp.state.analytics.mark_promoted"),
            patch(
                "trw_mcp.state.claude_md._sync.generate_review_md",
                return_value={
                    "path": str(tmp_path / "REVIEW.md"),
                    "rules_count": 0,
                    "status": "generated",
                },
            ) as mock_gen,
        ):
            result = execute_claude_md_sync(**args)  # type: ignore[arg-type]

        assert "review_md" in result
        assert result["review_md"]["status"] == "generated"
        mock_gen.assert_called_once()

    def test_sync_continues_if_review_md_fails(self, tmp_path: Path) -> None:
        """If generate_review_md raises, sync still completes (fail-open)."""
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = self._make_sync_args(tmp_path)

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch("trw_mcp.state.analytics.mark_promoted"),
            patch("trw_mcp.state.claude_md._sync.generate_review_md", side_effect=RuntimeError("boom")),
        ):
            result = execute_claude_md_sync(**args)  # type: ignore[arg-type]

        # Sync should still succeed
        assert result["status"] == "synced"
        # review_md should indicate failure
        assert result["review_md"]["status"] == "failed"

    def test_sync_passes_correct_args_to_review_md(self, tmp_path: Path) -> None:
        """execute_claude_md_sync passes trw_dir and repo_root to generate_review_md."""
        from trw_mcp.state.claude_md._sync import execute_claude_md_sync

        args = self._make_sync_args(tmp_path)
        trw_dir = tmp_path / ".trw"

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch("trw_mcp.state.analytics.mark_promoted"),
            patch(
                "trw_mcp.state.claude_md._sync.generate_review_md",
                return_value={
                    "path": str(tmp_path / "REVIEW.md"),
                    "rules_count": 0,
                    "status": "generated",
                },
            ) as mock_gen,
        ):
            execute_claude_md_sync(**args)  # type: ignore[arg-type]

        mock_gen.assert_called_once_with(trw_dir, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# Unit test: _get_repo_root
# ---------------------------------------------------------------------------


class TestGetRepoRoot:
    """Tests for _get_repo_root helper."""

    def test_returns_path_on_success(self) -> None:
        from trw_mcp.state.claude_md._sync import _get_repo_root

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/project\n"

        with patch("trw_mcp.state.claude_md._sync.subprocess.run", return_value=mock_result):
            result = _get_repo_root()

        assert result == Path("/home/user/project")

    def test_returns_none_on_failure(self) -> None:
        from trw_mcp.state.claude_md._sync import _get_repo_root

        mock_result = MagicMock()
        mock_result.returncode = 128

        with patch("trw_mcp.state.claude_md._sync.subprocess.run", return_value=mock_result):
            result = _get_repo_root()

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        from trw_mcp.state.claude_md._sync import _get_repo_root

        with patch(
            "trw_mcp.state.claude_md._sync.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = _get_repo_root()

        assert result is None
