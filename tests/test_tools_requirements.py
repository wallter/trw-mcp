"""Tests for requirements tools — prd_create, prd_validate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    # Reset template cache so each test starts fresh
    import trw_mcp.tools.requirements as req_mod

    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)

    # Create .trw/ so prd_create knows it's a TRW project
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    return get_tools_sync(make_test_server("requirements"))


class TestTrwPrdCreate:
    """Tests for trw_prd_create tool."""

    def test_creates_prd(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Add user authentication with OAuth2 support",
            category="CORE",
            priority="P1",
            title="User Authentication",
        )
        assert result["prd_id"] == "PRD-CORE-001"
        assert result["title"] == "User Authentication"
        assert result["sections_generated"] == 12
        assert "content" in result

        # Verify content structure
        content = result["content"]
        assert "---" in content
        assert "Problem Statement" in content
        assert "Goals & Non-Goals" in content
        assert "Traceability Matrix" in content

    def test_auto_generates_title(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Implement caching layer for API responses",
            category="INFRA",
        )
        assert result["title"] == "Implement caching layer for API responses"

    def test_saves_to_disk(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature request",
            category="CORE",
            title="Test Feature",
        )
        assert result["output_path"] != ""
        assert Path(result["output_path"]).exists()

    def test_invalid_priority(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        tools = _get_tools()
        with pytest.raises(ValidationError, match="Invalid priority"):
            tools["trw_prd_create"].fn(
                input_text="test",
                priority="P99",
            )

    def test_priority_affects_confidence(self, tmp_path: Path) -> None:
        """P0 → 0.9, P1 → 0.7, P2 → 0.6, P3 → 0.5 in both frontmatter and body."""
        tools = _get_tools()
        for priority, expected in [("P0", 0.9), ("P1", 0.7), ("P2", 0.6), ("P3", 0.5)]:
            result = tools["trw_prd_create"].fn(
                input_text=f"Test for {priority}",
                priority=priority,
                title=f"Confidence {priority}",
                sequence=int(priority[1]) + 10,  # avoid collisions
            )
            content = result["content"]
            assert f"**Implementation Confidence**: {expected}" in content
            assert f"**Priority**: {priority}" in content

    def test_auto_increments_sequence(self, tmp_path: Path) -> None:
        """When sequence=1 (default), auto-increment from existing PRDs."""
        tools = _get_tools()

        # Create first PRD
        r1 = tools["trw_prd_create"].fn(
            input_text="First PRD",
            category="CORE",
            title="First",
        )
        assert r1["prd_id"] == "PRD-CORE-001"

        # Create second PRD with default sequence — should auto-increment
        r2 = tools["trw_prd_create"].fn(
            input_text="Second PRD",
            category="CORE",
            title="Second",
        )
        assert r2["prd_id"] == "PRD-CORE-002"

    def test_explicit_sequence_not_overridden(self, tmp_path: Path) -> None:
        """When sequence > 1 is explicitly set, use it as-is."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Explicit sequence PRD",
            category="CORE",
            title="Explicit",
            sequence=42,
        )
        assert result["prd_id"] == "PRD-CORE-042"


class TestTrwPrdValidate:
    """Tests for trw_prd_validate tool."""

    def test_validates_good_prd(self, tmp_path: Path) -> None:
        # Create a well-formed PRD
        prd_content = """---
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
We need to solve X.

## 2. Goals & Non-Goals
Goals and non-goals.

## 3. User Stories
User stories here.

## 4. Functional Requirements
Requirements.

## 5. Non-Functional Requirements
NFRs.

## 6. Technical Approach
Approach.

## 7. Test Strategy
Testing.

## 8. Rollout Plan
Rollout.

## 9. Success Metrics
Metrics.

## 10. Dependencies & Risks
Risks.

## 11. Open Questions
Questions.

## 12. Traceability Matrix
Matrix.
"""
        prd_path = tmp_path / "test.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is True
        assert len(result["sections_found"]) == 12

    def test_validates_incomplete_prd(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-002
  title: "Incomplete"
---

# Incomplete PRD

## 1. Problem Statement
Only one section.
"""
        prd_path = tmp_path / "incomplete.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is False
        assert len(result["failures"]) > 0

    def test_detects_low_density(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-003
  title: "Sparse"
  version: "1.0"
  status: draft
  priority: P1

traceability:
  implements: [KE-001]
---

# PRD-CORE-003: Sparse PRD

## 1. Problem Statement
The system should be fast.

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
        prd_path = tmp_path / "sparse.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        # Sparse PRD should have low completeness or section density issues
        assert result["total_score"] < 80.0

    def test_file_not_found(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import StateError

        tools = _get_tools()
        with pytest.raises(StateError, match="not found"):
            tools["trw_prd_validate"].fn(prd_path=str(tmp_path / "nonexistent.md"))


class TestTemplateLoading:
    """Tests for _load_template_body() and template caching."""

    def test_returns_string(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert isinstance(body, str)
        assert len(body) > 100

    def test_strips_frontmatter(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        # Body should NOT start with --- (frontmatter stripped)
        assert not body.startswith("---")
        # Should not contain the YAML frontmatter id pattern
        assert "id: PRD-{CATEGORY}-{SEQUENCE}" not in body

    def test_caching_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools.requirements as req_mod
        from trw_mcp.tools.requirements import _load_template_body

        # First call populates cache
        body1 = _load_template_body()
        assert req_mod._CACHED_TEMPLATE_BODY is not None

        # Second call returns same object (cached)
        body2 = _load_template_body()
        assert body1 is body2

    def test_contains_quality_checklist(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert "Quality Checklist" in body

    def test_contains_appendix(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert "## Appendix" in body

    def test_contains_nfr03_security(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert "NFR03: Security" in body

    def test_contains_acceptance_tests(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert "Acceptance Tests" in body

    def test_contains_phase3_release(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body

        body = _load_template_body()
        assert "Phase 3: Release" in body

    def test_fallback_on_missing_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools.requirements as req_mod

        # Reset cache
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)

        # Patch Path to make template "not found"
        original_parent = Path(req_mod.__file__).parent.parent / "data" / "prd_template.md"

        def fake_exists(self: Path) -> bool:
            if str(self) == str(original_parent):
                return False
            return (
                Path.exists.__wrapped__(self) if hasattr(Path.exists, "__wrapped__") else type(self).exists.fget(self)
            )  # type: ignore[attr-defined]

        # Use monkeypatch to make the template path non-existent
        import unittest.mock

        with unittest.mock.patch.object(Path, "exists", return_value=False):
            # Reset again inside the mock
            req_mod._CACHED_TEMPLATE_BODY = None
            req_mod._CACHED_TEMPLATE_VERSION = None
            body = req_mod._load_template_body()

        assert isinstance(body, str)
        # Fallback should still have the 12 sections
        assert "Problem Statement" in body
        assert req_mod._CACHED_TEMPLATE_VERSION is None


class TestTemplateVersionExtraction:
    """Tests for template version extraction."""

    def test_version_extracted_correctly(self) -> None:
        import trw_mcp.tools.requirements as req_mod
        from trw_mcp.tools.requirements import _load_template_body

        _load_template_body()
        assert req_mod._CACHED_TEMPLATE_VERSION == "2.2"

    def test_version_none_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools.requirements as req_mod

        # Set fallback body (no version footer)
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)

        import unittest.mock

        with unittest.mock.patch.object(Path, "exists", return_value=False):
            req_mod._CACHED_TEMPLATE_BODY = None
            req_mod._CACHED_TEMPLATE_VERSION = None
            req_mod._load_template_body()

        assert req_mod._CACHED_TEMPLATE_VERSION is None


class TestTemplateSubstitution:
    """Tests for _substitute_template()."""

    def test_category_replaced(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body, _substitute_template

        body = _load_template_body()
        result = _substitute_template(body, "PRD-CORE-007", "Test", "CORE", 7, "P1", 0.7)
        assert "{CATEGORY}" not in result
        assert "{CAT}" not in result

    def test_sequence_replaced(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body, _substitute_template

        body = _load_template_body()
        result = _substitute_template(body, "PRD-CORE-007", "Test", "CORE", 7, "P1", 0.7)
        assert "{SEQUENCE}" not in result
        assert "{SEQ}" not in result
        assert "007" in result

    def test_title_replaced(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body, _substitute_template

        body = _load_template_body()
        result = _substitute_template(body, "PRD-CORE-007", "My Feature", "CORE", 7, "P1", 0.7)
        assert "{Title}" not in result
        assert "My Feature" in result

    def test_quick_reference_values(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body, _substitute_template

        body = _load_template_body()
        result = _substitute_template(body, "PRD-FIX-003", "Fix", "FIX", 3, "P0", 0.9)
        assert "**Priority**: P0" in result
        assert "**Implementation Confidence**: 0.9" in result
        assert "**Status**: Draft" in result
        assert "**Evidence**: Moderate" in result

    def test_prose_placeholders_left_intact(self) -> None:
        from trw_mcp.tools.requirements import _load_template_body, _substitute_template

        body = _load_template_body()
        result = _substitute_template(body, "PRD-CORE-001", "Test", "CORE", 1, "P1", 0.7)
        # Prose placeholders like {Brief context...} should remain
        assert "{Brief context" in result or "{Clear statement" in result


class TestPrefillExtraction:
    """Tests for _extract_prefill()."""

    def test_file_refs_extracted(self) -> None:
        from trw_mcp.tools.requirements import _extract_prefill

        prefill = _extract_prefill("Modify tools/requirements.py and models/config.py")
        assert "tools/requirements.py" in prefill["file_refs"]
        assert "models/config.py" in prefill["file_refs"]

    def test_prd_deps_extracted(self) -> None:
        from trw_mcp.tools.requirements import _extract_prefill

        prefill = _extract_prefill("This depends on PRD-CORE-007 and PRD-FIX-006")
        assert "PRD-CORE-007" in prefill["prd_deps"]
        assert "PRD-FIX-006" in prefill["prd_deps"]

    def test_goals_extracted(self) -> None:
        from trw_mcp.tools.requirements import _extract_prefill

        prefill = _extract_prefill("The goal is to improve test coverage. We want to achieve 90%.")
        assert len(prefill["goals"]) >= 1

    def test_slos_extracted(self) -> None:
        from trw_mcp.tools.requirements import _extract_prefill

        prefill = _extract_prefill("SLO: latency under 200ms. Availability target 99.9%.")
        assert len(prefill["slos"]) >= 1

    def test_empty_input_ok(self) -> None:
        from trw_mcp.tools.requirements import _extract_prefill

        prefill = _extract_prefill("")
        assert prefill["file_refs"] == []
        assert prefill["prd_deps"] == []
        assert prefill["goals"] == []
        assert prefill["slos"] == []


class TestPrefillApplication:
    """Tests for _apply_prefill()."""

    def test_input_text_in_background(self) -> None:
        from trw_mcp.tools.requirements import (
            _apply_prefill,
            _extract_prefill,
            _load_template_body,
            _substitute_template,
        )

        body = _load_template_body()
        body = _substitute_template(body, "PRD-CORE-001", "Test", "CORE", 1, "P1", 0.7)
        prefill = _extract_prefill("My feature description here")
        result = _apply_prefill(body, prefill, "My feature description here")
        assert "My feature description here" in result

    def test_file_refs_in_section6(self) -> None:
        from trw_mcp.tools.requirements import (
            _apply_prefill,
            _load_template_body,
            _substitute_template,
        )

        body = _load_template_body()
        body = _substitute_template(body, "PRD-CORE-001", "Test", "CORE", 1, "P1", 0.7)
        prefill = {"file_refs": ["tools/requirements.py"], "prd_deps": [], "goals": [], "slos": []}
        result = _apply_prefill(body, prefill, "input")
        assert "`tools/requirements.py`" in result

    def test_prd_deps_in_section10(self) -> None:
        from trw_mcp.tools.requirements import (
            _apply_prefill,
            _load_template_body,
            _substitute_template,
        )

        body = _load_template_body()
        body = _substitute_template(body, "PRD-CORE-001", "Test", "CORE", 1, "P1", 0.7)
        prefill = {"file_refs": [], "prd_deps": ["PRD-FIX-006"], "goals": [], "slos": []}
        result = _apply_prefill(body, prefill, "Depends on PRD-FIX-006")
        assert "PRD-FIX-006" in result


class TestModelFields:
    """Tests for new PRDFrontmatter fields."""

    def test_new_fields_accepted(self) -> None:
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(
            id="PRD-CORE-001",
            title="Test",
            template_version="2.1",
            wave_source="Wave 3",
            slos=["latency < 200ms"],
        )
        assert fm.template_version == "2.1"
        assert fm.wave_source == "Wave 3"
        assert fm.slos == ["latency < 200ms"]

    def test_backward_compat_defaults(self) -> None:
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(id="PRD-CORE-001", title="Test")
        assert fm.template_version is None
        assert fm.wave_source is None
        assert fm.slos == []


class TestIntegration:
    """Integration tests for the full template-driven pipeline."""

    def test_full_structure_has_all_sections(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Implement template-driven PRD generation",
            category="CORE",
            priority="P1",
            title="Template PRDs",
        )
        content = result["content"]
        assert result["sections_generated"] == 12

        # All 12 numbered sections
        for i, section in enumerate(
            [
                "Problem Statement",
                "Goals & Non-Goals",
                "User Stories",
                "Functional Requirements",
                "Non-Functional Requirements",
                "Technical Approach",
                "Test Strategy",
                "Rollout Plan",
                "Success Metrics",
                "Dependencies & Risks",
                "Open Questions",
                "Traceability Matrix",
            ],
            1,
        ):
            assert f"## {i}. {section}" in content, f"Missing section {i}. {section}"

    def test_nfr03_security_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Security-relevant feature",
            category="CORE",
            title="Secure Feature",
        )
        assert "NFR03: Security" in result["content"]

    def test_appendix_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature with appendix",
            category="CORE",
            title="Appendix Test",
        )
        assert "## Appendix" in result["content"]

    def test_quality_checklist_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature with checklist",
            category="CORE",
            title="Checklist Test",
        )
        assert "Quality Checklist" in result["content"]

    def test_template_version_in_frontmatter(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Version check",
            category="CORE",
            title="Version Test",
        )
        assert "template_version" in result["content"]
        assert "2.2" in result["content"]

    def test_backward_compat_return_schema(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Schema check",
            category="CORE",
            title="Schema Test",
        )
        # All required keys present
        assert "prd_id" in result
        assert "title" in result
        assert "category" in result
        assert "priority" in result
        assert "output_path" in result
        assert "content" in result
        assert "sections_generated" in result
        assert result["sections_generated"] == 12

    def test_prefill_with_file_refs_and_deps(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Modify tools/requirements.py. Depends on PRD-FIX-006.",
            category="CORE",
            title="Prefill Test",
        )
        content = result["content"]
        assert "`tools/requirements.py`" in content
        assert "PRD-FIX-006" in content

    def test_slos_in_frontmatter(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="SLO: latency under 200ms for all API calls",
            category="CORE",
            title="SLO Test",
        )
        content = result["content"]
        assert "slos" in content
