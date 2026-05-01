"""Framework v25 portability regression guards (PRD-CORE-161)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRAMEWORK_ROOT = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_FRAMEWORK_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"


def _framework_content() -> str:
    if not _FRAMEWORK_ROOT.exists():
        pytest.skip("FRAMEWORK.md not found at expected path")
    return _FRAMEWORK_ROOT.read_text(encoding="utf-8")


class TestFrameworkV25Portability:
    """PRD-CORE-161: canonical framework is portable and evidence-led."""

    def test_header_declares_v25_model_agnostic_policy(self) -> None:
        content = _framework_content()
        assert "v25_TRW" in content
        assert "MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK" in content
        assert "Model policy: capability-based" in content

    def test_framework_removes_v24_provider_and_beta_claims(self) -> None:
        content = _framework_content()
        forbidden = ("Opus 4.7", "Opus 4.6", "Agent " + "Teams", "Team" + "Create", "Send" + "Message")
        for token in forbidden:
            assert token not in content, f"FRAMEWORK.md still contains retired v24/beta token: {token}"

    def test_framework_md_bundled_copy_matches_root(self) -> None:
        if not _FRAMEWORK_BUNDLED.exists():
            pytest.skip("bundled framework.md not found")
        assert _FRAMEWORK_ROOT.read_bytes() == _FRAMEWORK_BUNDLED.read_bytes(), (
            "bundled framework.md has drifted from .trw/frameworks/FRAMEWORK.md"
        )

    def test_eval_transfer_discipline_is_present(self) -> None:
        content = _framework_content()
        assert "eval and transfer discipline" in content.lower()
        assert "stratified" in content.lower()
        assert "harness" in content.lower()
        assert "uncertainty" in content.lower()

    def test_language_agnostic_validation_policy_is_present(self) -> None:
        content = _framework_content()
        assert "LANGUAGE-AGNOSTIC VALIDATION" in content
        assert "project-native" in content
        assert "do not invent universal percentages or single-language gates" in content

    def test_nudge_policy_is_present(self) -> None:
        content = _framework_content()
        assert "NUDGES AND ADAPTIVE GUIDANCE" in content
        assert "Nudges MUST be client-, model-, and language-neutral" in content
        assert "workflow" in content and "learnings" in content and "ceremony" in content and "context" in content


class TestFrameworkMdEnforcement:
    """Core RFC 2119 and process guardrails remain present in v25."""

    def test_rigid_review_must(self) -> None:
        content = _framework_content()
        assert "trw_review()" in content and "before DELIVER" in content

    def test_reversion_must_revert(self) -> None:
        content = _framework_content()
        assert "SHOULD revert" in content or "MUST revert" in content

    def test_phase_must_not_advance(self) -> None:
        content = _framework_content()
        assert "MUST NOT advance" in content

    def test_watchlist_review_entry(self) -> None:
        content = _framework_content()
        assert "RATIONALIZATION WATCHLIST" in content
        content_lower = content.lower()
        assert "too simple" in content_lower or "skip" in content_lower

    def test_phase_reversion_quality_signal(self) -> None:
        content = _framework_content()
        assert "PHASE REVERSION" in content
        assert "revert" in content.lower() and ("structural" in content.lower() or "redesign" in content.lower())
