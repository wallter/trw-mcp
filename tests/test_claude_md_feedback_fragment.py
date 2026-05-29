"""Tests for the feedback-reporting CLAUDE.md fragment (PRD-INFRA-132 FR02).

Covers:
- Full-mode block contains the marker sentinels, all 5 SubmissionCategory
  enum strings, the MCP tool name, and the skill invocation.
- Light-mode (ceremony_mode == "light") variant fits inside the
  PRD-INFRA-132 NFR03 120-char budget and points at the llms.txt anchor.
- ``feedback_skill is None`` opts a profile out of the section entirely
  (empty string returned; renderer dispatch should skip it).
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config._client_profile import ClientProfile, WriteTargets
from trw_mcp.state.claude_md.sections._feedback import (
    FEEDBACK_MARKER_END,
    FEEDBACK_MARKER_START,
    render_feedback_reporting,
)


def _profile(
    *,
    ceremony_mode: str = "full",
    feedback_skill: str | None = "trw-feedback",
) -> ClientProfile:
    return ClientProfile(
        client_id="test-client",
        display_name="Test Client",
        write_targets=WriteTargets(
            claude_md=True,
            instruction_path=".claude/INSTRUCTIONS.md",
        ),
        ceremony_mode=ceremony_mode,  # type: ignore[arg-type]
        feedback_skill=feedback_skill,
    )


class TestFullBlockForFullMode:
    """Full ceremony mode receives the marker-wrapped block."""

    def test_full_block_contains_marker_sentinels(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="full"))
        assert FEEDBACK_MARKER_START in out
        assert FEEDBACK_MARKER_END in out

    def test_full_block_contains_all_six_categories(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="full"))
        for category in (
            "bugfix",
            "installation",
            "feedback",
            "feature_request",
            "question",
            "other",
        ):
            assert category in out, f"missing category {category!r} in fragment"

    def test_full_block_names_mcp_tool_and_skill(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="full"))
        assert "trw_submit_feedback" in out
        assert "/trw-feedback" in out

    def test_full_block_includes_auth_note(self) -> None:
        """Operators need to know the channel is auth-gated (NFR05/PRD-CORE-182)."""
        out = render_feedback_reporting(_profile(ceremony_mode="full"))
        assert "platform_api_key" in out


class TestOneLineForLightMode:
    """Light ceremony mode receives a one-line ≤120-char variant (NFR03)."""

    def test_one_line_is_under_120_chars(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="light"))
        # Trailing newline is not part of the "instruction-budget" content.
        content = out.rstrip("\n")
        assert len(content) <= 120, f"NFR03 hard cap violated: {len(content)} > 120 chars: {content!r}"

    def test_one_line_points_at_llms_txt_anchor(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="light"))
        assert "trwframework.com" in out
        assert "reporting-issues-to-trw" in out

    def test_one_line_omits_full_block(self) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode="light"))
        assert FEEDBACK_MARKER_START not in out
        assert FEEDBACK_MARKER_END not in out
        # No category enumeration in the budget-constrained variant.
        assert "installation" not in out
        assert "feature_request" not in out


class TestLinkOnlyWhenFeedbackSkillNone:
    """PRD-INFRA-132 FR03 opt-out branch: a profile with
    ``feedback_skill=None`` still emits the link-only variant so the
    operator retains a discovery surface (audit P2-5 fix)."""

    @pytest.mark.parametrize("mode", ["full", "light"])
    def test_returns_link_only_variant(self, mode: str) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode=mode, feedback_skill=None))
        assert "trwframework.com" in out
        assert "reporting-issues-to-trw" in out
        # No marker block: this is a one-line fragment.
        assert FEEDBACK_MARKER_START not in out
        assert FEEDBACK_MARKER_END not in out
        # No skill invocation: the opt-out branch must not advertise a slash command.
        assert "/trw-feedback" not in out

    @pytest.mark.parametrize("mode", ["full", "light"])
    def test_opt_out_honors_nfr03_cap(self, mode: str) -> None:
        out = render_feedback_reporting(_profile(ceremony_mode=mode, feedback_skill=None))
        assert len(out.rstrip("\n")) <= 120


class TestLightModeCapHoldsForLongCustomSkillName:
    """NFR03 hard cap MUST hold even for the longest realistically-allowed
    custom ``feedback_skill`` value (audit P1-2 fix)."""

    def test_light_mode_120_char_cap_with_long_custom_skill(self) -> None:
        # 50-char custom skill name (well above the default 12).
        long_skill = "trw-feedback-very-long-custom-handle-for-cap-test"
        out = render_feedback_reporting(
            _profile(ceremony_mode="light", feedback_skill=long_skill)
        )
        assert len(out.rstrip("\n")) <= 120, (
            f"NFR03 cap violated for custom skill {long_skill!r}: "
            f"{len(out.rstrip())} chars in {out!r}"
        )
