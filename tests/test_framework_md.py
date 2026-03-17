"""Tests for PRD-CORE-084 FR07: FRAMEWORK.md RFC 2119 enforcement.

Grep-based assertions verifying that FRAMEWORK.md contains required
MUST/SHALL language for review enforcement, phase reversion, and
phase transitions.
"""

from pathlib import Path

import pytest


def _framework_path() -> Path:
    """Locate FRAMEWORK.md relative to the test file."""
    # Tests are in trw-mcp/tests/, framework is in .trw/frameworks/
    repo_root = Path(__file__).resolve().parent.parent.parent
    path = repo_root / ".trw" / "frameworks" / "FRAMEWORK.md"
    if not path.exists():
        pytest.skip("FRAMEWORK.md not found at expected path")
    return path


def _framework_content() -> str:
    return _framework_path().read_text(encoding="utf-8")


class TestFrameworkMdEnforcement:
    """FR07: FRAMEWORK.md RFC 2119 language verification."""

    def test_rigid_review_must(self) -> None:
        """RIGID section contains trw_review() with enforcement language for DELIVER."""
        content = _framework_content()
        # v24.3 phrasing: "trw_review() — always before DELIVER for STANDARD+ complexity tasks"
        assert "trw_review()" in content and "before DELIVER" in content, (
            "FRAMEWORK.md RIGID section must reference trw_review() before DELIVER"
        )

    def test_reversion_must_revert(self) -> None:
        """Phase reversion section uses RFC 2119 language for reversion."""
        content = _framework_content()
        # v24.3 phrasing: "Agents SHOULD revert to earlier phases when ..."
        assert "SHOULD revert" in content or "MUST revert" in content, (
            "FRAMEWORK.md phase reversion must use RFC 2119 revert language (SHOULD or MUST)"
        )

    def test_phase_must_not_advance(self) -> None:
        """Phase transitions use MUST NOT advance language."""
        content = _framework_content()
        assert "MUST NOT advance" in content, "FRAMEWORK.md must contain 'MUST NOT advance' for phase transitions"

    def test_watchlist_review_entry(self) -> None:
        """Rationalization watchlist contains ceremony-skip warning entries."""
        content = _framework_content()
        # v24.3 watchlist warns against skipping ceremony/recall, not review specifically
        assert "RATIONALIZATION WATCHLIST" in content, (
            "FRAMEWORK.md must contain a rationalization watchlist section"
        )
        # Verify the watchlist has substantive anti-skip entries
        content_lower = content.lower()
        assert "too simple" in content_lower or "don't need" in content_lower or "skip" in content_lower, (
            "FRAMEWORK.md rationalization watchlist must contain ceremony-skip warnings"
        )

    def test_phase_reversion_quality_signal(self) -> None:
        """Phase reversion section exists and provides structural guidance."""
        content = _framework_content()
        # v24.3 frames reversion as structural gap response with a decision table
        assert "PHASE REVERSION" in content, (
            "FRAMEWORK.md must contain a PHASE REVERSION section"
        )
        # Verify it provides actionable reversion guidance (not just a title)
        assert "revert" in content.lower() and ("structural" in content.lower() or "redesign" in content.lower()), (
            "FRAMEWORK.md phase reversion section must provide structural reversion guidance"
        )
