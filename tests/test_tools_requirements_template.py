"""Template helper and model field tests for requirements tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_tools_requirements_support import set_project_root  # noqa: F401


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
        assert not body.startswith("---")
        assert "id: PRD-{CATEGORY}-{SEQUENCE}" not in body

    def test_caching_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools.requirements import _load_template_body

        body1 = _load_template_body()
        assert helpers._CACHED_TEMPLATE_BODY is not None

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
        import unittest.mock

        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools._prd_template_helpers import reset_template_cache

        reset_template_cache()
        with unittest.mock.patch.object(Path, "exists", return_value=False):
            reset_template_cache()
            from trw_mcp.tools.requirements import _load_template_body

            body = _load_template_body()

        assert isinstance(body, str)
        assert "Problem Statement" in body
        assert helpers._CACHED_TEMPLATE_VERSION is None


class TestTemplateVersionExtraction:
    """Tests for template version extraction."""

    def test_version_extracted_correctly(self) -> None:
        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools.requirements import _load_template_body

        _load_template_body()
        assert helpers._CACHED_TEMPLATE_VERSION == "2.3"

    def test_version_none_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import unittest.mock

        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools._prd_template_helpers import reset_template_cache

        reset_template_cache()
        with unittest.mock.patch.object(Path, "exists", return_value=False):
            reset_template_cache()
            from trw_mcp.tools.requirements import _load_template_body

            _load_template_body()

        assert helpers._CACHED_TEMPLATE_VERSION is None


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
        assert "{Brief context" in result or "{Clear statement" in result


class TestInlineTemplateParity:
    """PRD-QUAL-092 FR02: the inline fallback template carries FPI #7 fields."""

    def test_inline_template_has_functionality_level_and_stubs(self) -> None:
        from trw_mcp.resources.templates import _INLINE_PRD_TEMPLATE

        assert "functionality_level:" in _INLINE_PRD_TEMPLATE
        assert "stubs:" in _INLINE_PRD_TEMPLATE
        # FPI #7 guidance comment parity with the canonical template.
        assert "FPI #7" in _INLINE_PRD_TEMPLATE

    def test_inline_fallback_body_has_functionality_level(self) -> None:
        """When the bundled template file is absent, get_prd_template returns the
        inline body and that body contains functionality_level: and stubs:.
        """
        import unittest.mock

        from fastmcp import FastMCP

        from tests.conftest import get_resources_sync
        from trw_mcp.resources.templates import register_template_resources

        server = FastMCP("test")
        register_template_resources(server)

        resources = get_resources_sync(server)
        prd_resource = resources["trw://templates/prd"]

        # Force the fallback path: bundled prd_template.md "missing".
        with unittest.mock.patch.object(Path, "exists", return_value=False):
            body = prd_resource.fn()

        assert "functionality_level:" in body
        assert "stubs:" in body


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
