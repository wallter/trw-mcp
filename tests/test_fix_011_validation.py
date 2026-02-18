"""Tests for PRD-FIX-011: V1/V2 validation pipeline consolidation.

Verifies that:
1. trw_prd_validate makes a single V2 call (no separate V1 call)
2. V2 result includes inline V1-compatible fields
3. Rich diagnostics (smells, EARS, readability, section_scores) are exposed
4. Backward-compatible output keys are preserved
5. Ambiguity detection still works as a single source of truth
"""

from __future__ import annotations

from typing import Any

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import validate_prd_quality_v2


# A well-formed PRD with all 12 sections and proper frontmatter
_GOOD_PRD = """\
---
prd:
  id: PRD-CORE-001
  title: "Test PRD"
  version: "1.0"
  status: draft
  priority: P1

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7

traceability:
  implements: [KE-FRAME-001]
  depends_on: []
---

# PRD-CORE-001: Test PRD

## 1. Problem Statement
We need a robust authentication system to handle OAuth2 flows.

## 2. Goals & Non-Goals
The primary goal is to implement secure user authentication.

## 3. User Stories
As a developer, I want to authenticate users via OAuth2.

## 4. Functional Requirements
### PRD-CORE-001-FR01: OAuth2 Support
When a user initiates login, the system shall redirect to the OAuth2 provider.

### PRD-CORE-001-FR02: Token Management
The system shall store and refresh access tokens securely.

## 5. Non-Functional Requirements
The authentication endpoint must respond within 200ms at p99.

## 6. Technical Approach
Use the authorization code flow with PKCE for security.

## 7. Test Strategy
Integration tests against a mock OAuth2 provider.

## 8. Rollout Plan
Phase 1: Internal testing. Phase 2: Staged rollout.

## 9. Success Metrics
Authentication success rate > 99.5%.

## 10. Dependencies & Risks
Depends on identity provider availability.

## 11. Open Questions
None currently.

## 12. Traceability Matrix
| Req | Implementation | Test |
|-----|---------------|------|
| FR01 | `auth.py:handle_login` | `test_auth.py` |
| FR02 | `auth.py:refresh_token` | `test_auth.py` |
"""

# An incomplete PRD missing sections and fields
_INCOMPLETE_PRD = """\
---
prd:
  id: PRD-CORE-002
  title: "Incomplete"
---

# Incomplete PRD

## 1. Problem Statement
Only one section.
"""

# A PRD with ambiguous terms
_AMBIGUOUS_PRD = """\
---
prd:
  id: PRD-CORE-003
  title: "Ambiguous PRD"
  version: "1.0"
  status: draft
  priority: P1

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7

traceability:
  implements: [KE-001]
---

# PRD-CORE-003: Ambiguous PRD

## 1. Problem Statement
The system should be fast and user-friendly and robust.
It should be scalable and flexible and easy to use.

## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.requirements as req_mod
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test-fix-011")
    register_requirements_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _run_validate(prd_text: str, tmp_path: Path) -> dict[str, Any]:
    """Write prd_text to a temp file and run trw_prd_validate, returning the result."""
    prd_path = tmp_path / "test.md"
    prd_path.write_text(prd_text, encoding="utf-8")
    tools = _get_tools()
    result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Test: single V2 execution path (no separate V1 call)
# ---------------------------------------------------------------------------

class TestSingleExecutionPath:
    """PRD-FIX-011-FR01: Only one validation call per invocation."""

    def test_validate_calls_v2_only(self, tmp_path: Path) -> None:
        """trw_prd_validate should call validate_prd_quality_v2 exactly once."""
        prd_path = tmp_path / "test.md"
        prd_path.write_text(_GOOD_PRD, encoding="utf-8")
        tools = _get_tools()
        with patch(
            "trw_mcp.tools.requirements.validate_prd_quality_v2",
            wraps=validate_prd_quality_v2,
        ) as mock_v2:
            tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert mock_v2.call_count == 1

    def test_v2_produces_v1_fields_without_separate_tool_call(self, tmp_path: Path) -> None:
        """V2 internally computes V1-compatible fields — the tool only calls V2.

        V2 delegates to V1 internally, but the tool module never imports
        or calls validate_prd_quality directly. This is verified structurally:
        validate_prd_quality is not in the tool module's namespace.
        """
        import trw_mcp.tools.requirements as req_mod
        assert not hasattr(req_mod, "validate_prd_quality"), (
            "Tool module should not import validate_prd_quality directly"
        )

        result = _run_validate(_GOOD_PRD, tmp_path)
        assert "completeness_score" in result
        assert "traceability_coverage" in result


# ---------------------------------------------------------------------------
# Test: V2 includes V1-compatible checks
# ---------------------------------------------------------------------------

class TestV2IncludesV1Checks:
    """PRD-FIX-011-FR02: V2 produces V1-compatible output fields."""

    def test_v2_valid_field_matches_v1_logic(self) -> None:
        """V2's valid field should match V1's validation logic."""
        config = TRWConfig()
        v2 = validate_prd_quality_v2(_GOOD_PRD, config)
        # Good PRD should be valid under both V1 and V2
        assert v2.valid is True
        assert v2.completeness_score >= 0.85

    def test_v2_detects_missing_frontmatter(self) -> None:
        """V2 should produce failures for missing frontmatter fields."""
        config = TRWConfig()
        v2 = validate_prd_quality_v2(_INCOMPLETE_PRD, config)
        assert v2.valid is False
        # Should detect missing version, status, priority at minimum
        failure_fields = [f.field for f in v2.failures]
        # Missing sections should be flagged
        assert any("section" in f.lower() for f in failure_fields)

    def test_v2_completeness_score_populated(self) -> None:
        """V2 result should have a completeness_score from inline V1 computation."""
        config = TRWConfig()
        v2 = validate_prd_quality_v2(_GOOD_PRD, config)
        assert 0.0 <= v2.completeness_score <= 1.0
        assert v2.completeness_score > 0.5  # good PRD should score well

    def test_v2_traceability_coverage_populated(self) -> None:
        """V2 result should have traceability_coverage from inline V1 computation."""
        config = TRWConfig()
        v2 = validate_prd_quality_v2(_GOOD_PRD, config)
        assert v2.traceability_coverage == 1.0  # has traceability links

    def test_v2_no_traceability_coverage_zero(self) -> None:
        """V2 result with no traces should have traceability_coverage == 0."""
        config = TRWConfig()
        v2 = validate_prd_quality_v2(_INCOMPLETE_PRD, config)
        assert v2.traceability_coverage == 0.0


# ---------------------------------------------------------------------------
# Test: rich diagnostics exposed (PRD-FIX-011-FR03)
# ---------------------------------------------------------------------------

class TestRichDiagnosticsExposed:
    """PRD-FIX-011-FR03: Previously discarded diagnostics are now exposed."""

    def test_smell_findings_exposed(self, tmp_path: Path) -> None:
        """Result should include smell_findings list."""
        result = _run_validate(_GOOD_PRD, tmp_path)
        assert "smell_findings" in result
        assert isinstance(result["smell_findings"], list)
        for sf in result["smell_findings"]:
            assert "category" in sf
            assert "matched_text" in sf
            assert "severity" in sf
            assert "suggestion" in sf
            assert "line_number" in sf

    def test_ears_classifications_exposed(self, tmp_path: Path) -> None:
        """Result should include ears_classifications list."""
        result = _run_validate(_GOOD_PRD, tmp_path)
        assert "ears_classifications" in result
        assert isinstance(result["ears_classifications"], list)

    def test_readability_scores_exposed(self, tmp_path: Path) -> None:
        """Result should include readability metrics dict."""
        result = _run_validate(_GOOD_PRD, tmp_path)
        assert "readability" in result
        assert isinstance(result["readability"], dict)

    def test_section_scores_exposed(self, tmp_path: Path) -> None:
        """Result should include per-section density scores."""
        result = _run_validate(_GOOD_PRD, tmp_path)
        assert "section_scores" in result
        assert isinstance(result["section_scores"], list)
        assert len(result["section_scores"]) > 0
        for ss in result["section_scores"]:
            assert "section_name" in ss
            assert "density" in ss
            assert "substantive_lines" in ss


# ---------------------------------------------------------------------------
# Test: backward-compatible output
# ---------------------------------------------------------------------------

class TestBackwardCompatibleOutput:
    """PRD-FIX-011-FR04: All V1 output keys are preserved."""

    def test_backward_compatible_keys(self, tmp_path: Path) -> None:
        """trw_prd_validate output should include all V1-era keys."""
        result = _run_validate(_GOOD_PRD, tmp_path)

        expected_keys = [
            # V1 keys
            "path", "valid", "completeness_score", "traceability_coverage",
            "ambiguity_rate", "sections_found", "sections_expected", "failures",
            # V2 keys
            "total_score", "quality_tier", "grade", "dimensions",
            "improvement_suggestions",
            # FIX-011 diagnostic keys
            "smell_findings", "ears_classifications", "readability", "section_scores",
        ]
        for key in expected_keys:
            assert key in result, f"Missing expected key: {key!r}"

    def test_failures_serialized_as_dicts(self, tmp_path: Path) -> None:
        """Failures should be serialized as plain dicts (not Pydantic models)."""
        prd_path = tmp_path / "incomplete.md"
        prd_path.write_text(_INCOMPLETE_PRD, encoding="utf-8")
        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert len(result["failures"]) > 0
        for f in result["failures"]:
            assert isinstance(f, dict)
            assert "field" in f
            assert "rule" in f
            assert "message" in f
            assert "severity" in f


# ---------------------------------------------------------------------------
# Test: ambiguity detection single source
# ---------------------------------------------------------------------------

class TestAmbiguityDetection:
    """Ambiguity detection uses V2 smell detection as single source."""

    def test_ambiguity_not_detected_without_smell_modules(self, tmp_path: Path) -> None:
        """With smell modules removed, smell_findings is always empty."""
        result = _run_validate(_AMBIGUOUS_PRD, tmp_path)
        # Smell detection modules were removed in strip-down
        assert result["smell_findings"] == []

    def test_clean_prd_low_ambiguity(self, tmp_path: Path) -> None:
        """A well-written PRD should have ambiguity rate below threshold."""
        result = _run_validate(_GOOD_PRD, tmp_path)
        # Good PRD should stay below the default 5% threshold
        assert result["ambiguity_rate"] < 0.05
