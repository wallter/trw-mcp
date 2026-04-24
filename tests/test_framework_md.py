"""Tests for PRD-CORE-084 FR07 and PRD-QUAL-072 FR03/FR08.

Grep-based assertions verifying that FRAMEWORK.md contains required
MUST/SHALL language for review enforcement, phase reversion, and
phase transitions; plus PRD-QUAL-072 regression guards on the Opus 4.7
header and the cross-link to the best-practices doc.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRAMEWORK_ROOT = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_FRAMEWORK_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"
_PROMPTING_HUB = _REPO_ROOT / "docs" / "documentation" / "prompting" / "CLAUDE.md"


def _framework_path() -> Path:
    """Locate FRAMEWORK.md relative to the test file."""
    if not _FRAMEWORK_ROOT.exists():
        pytest.skip("FRAMEWORK.md not found at expected path")
    return _FRAMEWORK_ROOT


def _framework_content() -> str:
    return _framework_path().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# PRD-QUAL-072 FR03/FR08 regression guards.
# ---------------------------------------------------------------------------


class TestQual072FrameworkMdRegression:
    """Regression guards for PRD-QUAL-072: FRAMEWORK.md Opus 4.7 header + cross-link."""

    def test_header_mentions_opus_47(self) -> None:
        """FRAMEWORK.md header must mention Opus 4.7 and must not mention Opus 4.6."""
        content = _framework_content()
        assert "Opus 4.7" in content, "FRAMEWORK.md must mention 'Opus 4.7'"
        assert "Opus 4.6" not in content, (
            "FRAMEWORK.md must not still mention 'Opus 4.6' after the 4.7 cutover"
        )

    def test_framework_md_bundled_copy_matches_root(self) -> None:
        """Bundled framework.md under trw-mcp/src/trw_mcp/data/ must be byte-identical to the canonical .trw/frameworks/FRAMEWORK.md."""
        if not _FRAMEWORK_BUNDLED.exists():
            pytest.skip("bundled framework.md not found")
        root_bytes = _FRAMEWORK_ROOT.read_bytes()
        bundled_bytes = _FRAMEWORK_BUNDLED.read_bytes()
        assert root_bytes == bundled_bytes, (
            "bundled framework.md has drifted from .trw/frameworks/FRAMEWORK.md — "
            "re-run the bundling step or sync the mirror"
        )

    def test_framework_md_links_best_practices_doc(self) -> None:
        """FRAMEWORK.md must cross-link to OPUS-4-7-BEST-PRACTICES."""
        content = _framework_content()
        assert "OPUS-4-7-BEST-PRACTICES" in content, (
            "FRAMEWORK.md must contain a link/reference to OPUS-4-7-BEST-PRACTICES"
        )

    def test_opus_47_best_practices_cross_linked_from_prompting_hub(self) -> None:
        """The prompting hub (docs/documentation/prompting/CLAUDE.md) must link the best-practices doc."""
        if not _PROMPTING_HUB.exists():
            pytest.skip("prompting hub CLAUDE.md not found")
        content = _PROMPTING_HUB.read_text(encoding="utf-8")
        assert "OPUS-4-7-BEST-PRACTICES" in content, (
            "docs/documentation/prompting/CLAUDE.md must cross-link OPUS-4-7-BEST-PRACTICES"
        )

    def test_opus_47_best_practices_cross_linked_from_framework_md(self) -> None:
        """Mirror of test_framework_md_links_best_practices_doc — kept separate for traceability."""
        content = _framework_content()
        assert "OPUS-4-7-BEST-PRACTICES" in content, (
            "FRAMEWORK.md must cross-link OPUS-4-7-BEST-PRACTICES (FR08 regression guard)"
        )


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
        assert "RATIONALIZATION WATCHLIST" in content, "FRAMEWORK.md must contain a rationalization watchlist section"
        # Verify the watchlist has substantive anti-skip entries
        content_lower = content.lower()
        assert "too simple" in content_lower or "don't need" in content_lower or "skip" in content_lower, (
            "FRAMEWORK.md rationalization watchlist must contain ceremony-skip warnings"
        )

    def test_phase_reversion_quality_signal(self) -> None:
        """Phase reversion section exists and provides structural guidance."""
        content = _framework_content()
        # v24.3 frames reversion as structural gap response with a decision table
        assert "PHASE REVERSION" in content, "FRAMEWORK.md must contain a PHASE REVERSION section"
        # Verify it provides actionable reversion guidance (not just a title)
        assert "revert" in content.lower() and ("structural" in content.lower() or "redesign" in content.lower()), (
            "FRAMEWORK.md phase reversion section must provide structural reversion guidance"
        )
