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
        """RIGID section contains trw_review() with MUST language."""
        content = _framework_content()
        assert "MUST run before DELIVER" in content, (
            "FRAMEWORK.md RIGID section must contain 'MUST run before DELIVER' for trw_review()"
        )

    def test_reversion_must_revert(self) -> None:
        """Phase reversion section uses MUST revert, not SHOULD revert."""
        content = _framework_content()
        assert "MUST revert" in content, (
            "FRAMEWORK.md phase reversion must use 'MUST revert' language"
        )

    def test_phase_must_not_advance(self) -> None:
        """Phase transitions use MUST NOT advance language."""
        content = _framework_content()
        assert "MUST NOT advance" in content, (
            "FRAMEWORK.md must contain 'MUST NOT advance' for phase transitions"
        )

    def test_watchlist_review_entry(self) -> None:
        """Rationalization watchlist contains review skip entry."""
        content = _framework_content()
        assert "don't need to run review" in content, (
            "FRAMEWORK.md rationalization watchlist must contain review skip entry"
        )

    def test_phase_reversion_quality_signal(self) -> None:
        """Phase reversion framed as quality signal."""
        content = _framework_content()
        assert "SIGN OF QUALITY" in content or "quality signal" in content.lower(), (
            "FRAMEWORK.md should frame phase reversion as a quality signal"
        )
