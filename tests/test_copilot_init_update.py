"""Copilot init/update integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._copilot_test_support import fake_git_repo  # noqa: F401


def _empty_update_result() -> dict[str, list[str]]:
    return {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
    }


@pytest.mark.unit
class TestCopilotInitProject:
    """Test init_project wiring for copilot."""

    def test_init_project_with_copilot_override(self, fake_git_repo: Path) -> None:
        """Call init_project with ide='copilot', verify copilot artifacts created."""
        from trw_mcp.bootstrap import init_project

        result = init_project(fake_git_repo, ide="copilot")
        assert not result["errors"]

        assert (fake_git_repo / ".github" / "copilot-instructions.md").is_file()
        assert (fake_git_repo / ".github" / "agents").is_dir()
        assert (fake_git_repo / ".github" / "hooks" / "hooks.json").is_file()
        assert (fake_git_repo / ".github" / "instructions").is_dir()

    def test_init_project_auto_detect_copilot(self, tmp_path: Path) -> None:
        """Create copilot detection artifacts first, then init_project auto-detects."""
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")

        result = init_project(tmp_path)
        assert not result["errors"]

        assert (tmp_path / ".github" / "copilot-instructions.md").is_file()
        assert (tmp_path / ".github" / "hooks" / "hooks.json").is_file()


@pytest.mark.unit
class TestCopilotUpdateProject:
    """Test _update_copilot_artifacts integration."""

    def test_update_copilot_artifacts(self, fake_git_repo: Path) -> None:
        """Call _update_copilot_artifacts, verify it runs without errors."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result = _empty_update_result()
        agents_dir = fake_git_repo / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")

        _update_copilot_artifacts(fake_git_repo, result)
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_copilot_artifacts_skips_without_detection(self, fake_git_repo: Path) -> None:
        """Without copilot artifacts detected, _update_copilot_artifacts is a no-op."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result = _empty_update_result()
        _update_copilot_artifacts(fake_git_repo, result)
        assert not result["created"]
        assert not result["errors"]

    def test_update_copilot_artifacts_with_override(self, fake_git_repo: Path) -> None:
        """Override to copilot even without detection artifacts."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result = _empty_update_result()
        _update_copilot_artifacts(fake_git_repo, result, ide_override="copilot")
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_copilot_artifacts_idempotent(self, fake_git_repo: Path) -> None:
        """Running update twice is safe — second run updates rather than errors."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        (fake_git_repo / ".github" / "agents").mkdir(parents=True)

        result1 = _empty_update_result()
        _update_copilot_artifacts(fake_git_repo, result1)
        assert not result1["errors"]

        result2 = _empty_update_result()
        _update_copilot_artifacts(fake_git_repo, result2)
        assert not result2["errors"]
