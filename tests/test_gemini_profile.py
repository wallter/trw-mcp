"""Tests for Gemini CLI profile registration, wiring, and detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._utils import SUPPORTED_IDES, detect_ide, resolve_ide_targets
from trw_mcp.models.config._profiles import _PROFILES, resolve_client_profile


@pytest.mark.unit
class TestGeminiProfile:
    """Verify the 'gemini' entry in the _PROFILES registry."""

    def test_gemini_profile_exists(self) -> None:
        assert "gemini" in _PROFILES

    def test_gemini_profile_is_full_mode(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.ceremony_mode == "full"

    def test_gemini_hooks_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.hooks_enabled is True

    def test_gemini_skills_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.skills_enabled is True

    def test_gemini_agents_md_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.agents_md is True

    def test_gemini_md_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.gemini_md is True

    def test_gemini_claude_md_disabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.claude_md is False

    def test_gemini_context_window(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.context_window_tokens == 1_000_000

    def test_gemini_ceremony_weights_sum_100(self) -> None:
        profile = _PROFILES["gemini"]
        w = profile.ceremony_weights
        total = w.session_start + w.deliver + w.checkpoint + w.learn + w.build_check + w.review
        assert total == 100

    def test_gemini_scoring_weights_sum_to_one(self) -> None:
        profile = _PROFILES["gemini"]
        sw = profile.scoring_weights
        total = sw.outcome + sw.plan_quality + sw.implementation + sw.ceremony + sw.knowledge
        assert abs(total - 1.0) < 0.01

    def test_gemini_instruction_path(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.instruction_path == "GEMINI.md"

    def test_gemini_display_name(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.display_name == "Google Gemini CLI"

    def test_gemini_client_id(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.client_id == "gemini"

    def test_resolve_client_profile_gemini(self) -> None:
        profile = resolve_client_profile("gemini")
        assert profile.client_id == "gemini"
        assert profile.ceremony_mode == "full"

    def test_gemini_nudge_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.nudge_enabled is True

    def test_gemini_learning_recall_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.learning_recall_enabled is True

    def test_gemini_mcp_instructions_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.mcp_instructions_enabled is True

    def test_gemini_profile_has_no_retired_beta_team_flag(self) -> None:
        """Gemini keeps portable delegation without the retired beta flag."""
        profile = _PROFILES["gemini"]
        assert not hasattr(profile, "include_agent" + "_teams")

    def test_gemini_delegation_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.include_delegation is True

    def test_gemini_framework_ref_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.include_framework_ref is True

    def test_gemini_in_supported_ides(self) -> None:
        assert "gemini" in SUPPORTED_IDES


@pytest.mark.unit
class TestGeminiProfileWiring:
    """Wiring tests proving profile weights produce different scoring output."""

    def test_gemini_vs_light_review_weight(self) -> None:
        """Full ceremony has non-zero review weight; light does not."""
        gemini = resolve_client_profile("gemini")
        opencode = resolve_client_profile("opencode")
        assert gemini.ceremony_weights.review > 0
        assert opencode.ceremony_weights.review == 0

    def test_gemini_vs_light_context_budget(self) -> None:
        """Gemini has far larger context budget than light profiles."""
        gemini = resolve_client_profile("gemini")
        aider = resolve_client_profile("aider")
        assert gemini.context_window_tokens > aider.context_window_tokens

    def test_gemini_vs_claude_code_context(self) -> None:
        """Gemini 1M context is larger than Claude Code 200K."""
        gemini = resolve_client_profile("gemini")
        claude = resolve_client_profile("claude-code")
        assert gemini.context_window_tokens > claude.context_window_tokens

    def test_gemini_and_copilot_have_no_retired_beta_team_flag(self) -> None:
        """v25 removes the retired beta team flag from built-in profiles."""
        gemini = resolve_client_profile("gemini")
        copilot = resolve_client_profile("copilot")
        assert not hasattr(gemini, "include_agent" + "_teams")
        assert not hasattr(copilot, "include_agent" + "_teams")


@pytest.mark.unit
class TestGeminiDetection:
    """Verify IDE detection recognizes Gemini CLI artifacts."""

    def test_detect_gemini_dir(self, tmp_path: Path) -> None:
        """Detect gemini via .gemini/ directory."""
        (tmp_path / ".gemini").mkdir()
        detected = detect_ide(tmp_path)
        assert "gemini" in detected

    def test_detect_gemini_md(self, tmp_path: Path) -> None:
        """Detect gemini via GEMINI.md file."""
        (tmp_path / "GEMINI.md").write_text("# My project\n")
        detected = detect_ide(tmp_path)
        assert "gemini" in detected

    def test_no_detect_without_artifacts(self, tmp_path: Path) -> None:
        """Clean dir should not detect gemini."""
        detected = detect_ide(tmp_path)
        assert "gemini" not in detected

    def test_detect_gemini_both_signals(self, tmp_path: Path) -> None:
        """Both .gemini/ dir and GEMINI.md present — still single detection."""
        (tmp_path / ".gemini").mkdir()
        (tmp_path / "GEMINI.md").write_text("# Project\n")
        detected = detect_ide(tmp_path)
        assert detected.count("gemini") == 1

    def test_resolve_ide_targets_gemini(self, tmp_path: Path) -> None:
        """When gemini artifacts exist, resolve_ide_targets includes 'gemini'."""
        (tmp_path / ".gemini").mkdir()
        targets = resolve_ide_targets(tmp_path)
        assert "gemini" in targets

    def test_resolve_ide_targets_gemini_override(self, tmp_path: Path) -> None:
        """Explicit ide_override='gemini' returns gemini even with no artifacts."""
        targets = resolve_ide_targets(tmp_path, ide_override="gemini")
        assert targets == ["gemini"]

    def test_resolve_ide_targets_all_includes_gemini(self, tmp_path: Path) -> None:
        """Override 'all' includes gemini."""
        targets = resolve_ide_targets(tmp_path, ide_override="all")
        assert "gemini" in targets
