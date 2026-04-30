"""Tests for Gemini bootstrap/update integration wiring."""

from __future__ import annotations

import pytest

from tests._gemini_test_support import fake_git_repo


@pytest.mark.unit
class TestGeminiInitProject:
    """Test init_project wiring for gemini."""

    def test_init_project_with_gemini_override(self, fake_git_repo) -> None:
        """Call init_project with ide='gemini', verify gemini artifacts created."""
        from trw_mcp.bootstrap import init_project

        result = init_project(fake_git_repo, ide="gemini")
        assert not result["errors"]

        assert (fake_git_repo / "GEMINI.md").is_file()
        assert (fake_git_repo / ".gemini" / "settings.json").is_file()
        assert (fake_git_repo / ".gemini" / "agents").is_dir()

    def test_init_project_auto_detect_gemini(self, tmp_path) -> None:
        """Create gemini detection artifacts first, then init_project auto-detects."""
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        (tmp_path / ".gemini").mkdir()

        result = init_project(tmp_path)
        assert not result["errors"]

        assert (tmp_path / "GEMINI.md").is_file()
        assert (tmp_path / ".gemini" / "settings.json").is_file()


@pytest.mark.unit
class TestGeminiUpdateArtifacts:
    """Test _update_gemini_artifacts integration."""

    def test_update_gemini_artifacts(self, fake_git_repo) -> None:
        """Call _update_gemini_artifacts, verify it runs without errors."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        (fake_git_repo / ".gemini").mkdir()

        _update_gemini_artifacts(fake_git_repo, result)
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_gemini_artifacts_skips_without_detection(self, fake_git_repo) -> None:
        """Without gemini artifacts detected, _update_gemini_artifacts is a no-op."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result)
        assert not result["created"]
        assert not result["errors"]

    def test_update_gemini_artifacts_with_override(self, fake_git_repo) -> None:
        """Override to gemini even without detection artifacts."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result, ide_override="gemini")
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_gemini_artifacts_idempotent(self, fake_git_repo) -> None:
        """Running update twice is safe — second run preserves rather than errors."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        (fake_git_repo / ".gemini").mkdir()

        result1: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result1)
        assert not result1["errors"]

        result2: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result2)
        assert not result2["errors"]
