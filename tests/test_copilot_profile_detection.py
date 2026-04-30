"""Copilot profile and detection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._utils import detect_ide, resolve_ide_targets
from trw_mcp.models.config._profiles import _PROFILES, resolve_client_profile


@pytest.mark.unit
class TestCopilotProfile:
    """Verify the 'copilot' entry in the _PROFILES registry."""

    def test_copilot_profile_exists(self) -> None:
        assert "copilot" in _PROFILES

    def test_copilot_profile_is_full_mode(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.ceremony_mode == "full"

    def test_copilot_hooks_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.hooks_enabled is True

    def test_copilot_skills_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.skills_enabled is True

    def test_copilot_agents_md_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.write_targets.agents_md is True

    def test_copilot_context_window(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.context_window_tokens == 200_000

    def test_copilot_ceremony_weights_sum_100(self) -> None:
        profile = _PROFILES["copilot"]
        weights = profile.ceremony_weights
        total = (
            weights.session_start
            + weights.deliver
            + weights.checkpoint
            + weights.learn
            + weights.build_check
            + weights.review
        )
        assert total == 100

    def test_copilot_scoring_weights_sum_to_one(self) -> None:
        profile = _PROFILES["copilot"]
        scoring = profile.scoring_weights
        total = (
            scoring.outcome
            + scoring.plan_quality
            + scoring.implementation
            + scoring.ceremony
            + scoring.knowledge
        )
        assert abs(total - 1.0) < 0.01

    def test_copilot_instruction_path(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.write_targets.instruction_path == ".github/copilot-instructions.md"

    def test_copilot_display_name(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.display_name == "GitHub Copilot CLI"

    def test_copilot_client_id(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.client_id == "copilot"

    def test_resolve_client_profile_copilot(self) -> None:
        profile = resolve_client_profile("copilot")
        assert profile.client_id == "copilot"
        assert profile.ceremony_mode == "full"

    def test_copilot_nudge_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.nudge_enabled is True

    def test_copilot_learning_recall_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.learning_recall_enabled is True

    def test_copilot_mcp_instructions_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.mcp_instructions_enabled is True


@pytest.mark.unit
class TestCopilotDetection:
    """Verify IDE detection recognizes Copilot artifacts."""

    def test_detect_copilot_instructions_file(self, tmp_path: Path) -> None:
        """Detect copilot via .github/copilot-instructions.md."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Instructions\n")
        detected = detect_ide(tmp_path)
        assert "copilot" in detected

    def test_detect_copilot_agents_dir(self, tmp_path: Path) -> None:
        """Detect copilot via .github/agents/ directory with .agent.md files."""
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "trw-explorer.agent.md").write_text("---\nname: test\n---\n")
        detected = detect_ide(tmp_path)
        assert "copilot" in detected

    def test_no_detect_agents_dir_without_agent_files(self, tmp_path: Path) -> None:
        """Empty .github/agents/ directory should NOT detect copilot."""
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        detected = detect_ide(tmp_path)
        assert "copilot" not in detected

    def test_no_detect_without_artifacts(self, tmp_path: Path) -> None:
        """Clean dir should not detect copilot."""
        detected = detect_ide(tmp_path)
        assert "copilot" not in detected

    def test_detect_copilot_both_signals(self, tmp_path: Path) -> None:
        """Both instructions file and agents dir present — still single detection."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Instructions\n")
        (github_dir / "agents").mkdir()
        detected = detect_ide(tmp_path)
        assert detected.count("copilot") == 1

    def test_resolve_ide_targets_copilot(self, tmp_path: Path) -> None:
        """When copilot artifacts exist, resolve_ide_targets includes 'copilot'."""
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")
        targets = resolve_ide_targets(tmp_path)
        assert "copilot" in targets

    def test_resolve_ide_targets_copilot_override(self, tmp_path: Path) -> None:
        """Explicit ide_override='copilot' returns copilot even with no artifacts."""
        targets = resolve_ide_targets(tmp_path, ide_override="copilot")
        assert targets == ["copilot"]

    def test_resolve_ide_targets_all_includes_copilot(self, tmp_path: Path) -> None:
        """Override 'all' includes copilot."""
        targets = resolve_ide_targets(tmp_path, ide_override="all")
        assert "copilot" in targets
