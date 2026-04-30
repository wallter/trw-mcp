"""Phase-tag mapping tests for auto-recall."""

from __future__ import annotations

from trw_mcp.tools._ceremony_helpers import _phase_to_tags


class TestPhaseToTags:
    """Phase-to-tags mapping helper function."""

    def test_research_phase(self) -> None:
        tags = _phase_to_tags("research")
        assert tags == ["architecture", "gotcha", "codebase"]

    def test_implement_phase(self) -> None:
        tags = _phase_to_tags("implement")
        assert tags == ["gotcha", "testing", "pattern"]

    def test_validate_phase(self) -> None:
        tags = _phase_to_tags("validate")
        assert tags == ["testing", "build", "coverage"]

    def test_review_phase(self) -> None:
        tags = _phase_to_tags("review")
        assert tags == ["security", "performance", "maintainability"]

    def test_unknown_phase_returns_empty(self) -> None:
        assert _phase_to_tags("unknown") == []

    def test_empty_phase_returns_empty(self) -> None:
        assert _phase_to_tags("") == []

    def test_plan_phase_returns_tags(self) -> None:
        """plan phase maps to architecture, pattern, dependency tags."""
        assert _phase_to_tags("plan") == ["architecture", "pattern", "dependency"]

    def test_deliver_phase_returns_tags(self) -> None:
        """deliver phase maps to ceremony, deployment, integration tags."""
        assert _phase_to_tags("deliver") == ["ceremony", "deployment", "integration"]
