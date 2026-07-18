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

    def test_repeated_loads_revalidate_and_return_equal_body(self) -> None:
        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools.requirements import _load_template_body

        body1 = _load_template_body()
        assert helpers._CACHED_TEMPLATE_BODY is not None

        body2 = _load_template_body()
        assert body1 == body2

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

    def test_missing_file_fails_closed(self) -> None:
        import unittest.mock

        from trw_mcp.tools._prd_template_helpers import reset_template_cache

        reset_template_cache()
        try:
            from trw_mcp.tools.requirements import _load_template_body

            with (
                unittest.mock.patch.object(Path, "is_file", return_value=False),
                pytest.raises(FileNotFoundError, match="canonical AARE-F PRD template"),
            ):
                _load_template_body()
        finally:
            reset_template_cache()


class TestTemplateVersionExtraction:
    """Tests for template version extraction."""

    def test_version_extracted_correctly(self) -> None:
        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools.requirements import _load_template_body

        _load_template_body()
        assert helpers._CACHED_TEMPLATE_VERSION == "3.2"

    def test_version_cache_remains_empty_when_missing(self) -> None:
        import unittest.mock

        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools._prd_template_helpers import reset_template_cache

        reset_template_cache()
        try:
            from trw_mcp.tools.requirements import _load_template_body

            with (
                unittest.mock.patch.object(Path, "is_file", return_value=False),
                pytest.raises(FileNotFoundError),
            ):
                _load_template_body()
            assert helpers._CACHED_TEMPLATE_VERSION is None
        finally:
            reset_template_cache()


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


class TestCanonicalTemplateFailure:
    """PRD-INFRA-164 FR06: missing canon never degrades to stale inline prose."""

    def test_resource_fails_closed_when_bundled_template_is_missing(self) -> None:
        import unittest.mock

        from fastmcp import FastMCP

        from tests.conftest import get_resources_sync
        from trw_mcp.resources.templates import register_template_resources

        server = FastMCP("test")
        register_template_resources(server)

        resources = get_resources_sync(server)
        prd_resource = resources["trw://templates/prd"]

        with (
            unittest.mock.patch.object(Path, "is_file", return_value=False),
            pytest.raises(FileNotFoundError, match="canonical AARE-F PRD template"),
        ):
            prd_resource.fn()

    def test_creator_fails_closed_when_bundled_template_is_missing(self) -> None:
        import unittest.mock

        from trw_mcp.tools._prd_template_helpers import _load_template_body, reset_template_cache

        reset_template_cache()
        try:
            with (
                unittest.mock.patch.object(Path, "is_file", return_value=False),
                pytest.raises(FileNotFoundError, match="canonical AARE-F PRD template"),
            ):
                _load_template_body()
        finally:
            reset_template_cache()

    @pytest.mark.parametrize("raw", ["not frontmatter", '---\ntemplate_version: "3.1"\n---\nbody'])
    def test_creator_rejects_malformed_or_wrong_version_template(self, raw: str) -> None:
        import unittest.mock

        from trw_mcp.tools._prd_template_helpers import _load_template_body, reset_template_cache

        reset_template_cache()
        try:
            with (
                unittest.mock.patch.object(Path, "is_file", return_value=True),
                unittest.mock.patch.object(Path, "read_text", return_value=raw),
                pytest.raises(ValueError, match=r"malformed or not version 3\.2"),
            ):
                _load_template_body()
        finally:
            reset_template_cache()

    def test_creator_does_not_reuse_cached_template_after_source_changes(self) -> None:
        import unittest.mock

        from trw_mcp.tools._prd_template_helpers import _load_template_body, reset_template_cache

        reset_template_cache()
        _load_template_body()
        try:
            with (
                unittest.mock.patch.object(Path, "is_file", return_value=True),
                unittest.mock.patch.object(Path, "read_text", return_value="changed invalid bytes"),
                pytest.raises(ValueError, match=r"malformed or not version 3\.2"),
            ):
                _load_template_body()
        finally:
            reset_template_cache()


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
