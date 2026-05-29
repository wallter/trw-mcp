"""Prefill extraction and application tests for requirements tools."""

from __future__ import annotations

from tests._test_tools_requirements_support import set_project_root  # noqa: F401


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
