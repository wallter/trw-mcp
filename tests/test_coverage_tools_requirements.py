from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.tools.requirements import register_requirements_tools

from tests._coverage_tools_support import _extract_tool, _make_server


class TestRequirementsFailurePaths:
    """Lines 122-126: invalid risk_level. Line 327: validate path. Lines 579-581: auto-sync failure."""

    def _register_and_get(self, name: str):
        server = _make_server()
        register_requirements_tools(server)
        return _extract_tool(server, name)

    def test_prd_create_invalid_risk_level_raises(self, tmp_path: Path) -> None:
        tool = self._register_and_get("trw_prd_create")

        with (
            patch("trw_mcp.tools.requirements.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.requirements.next_prd_sequence", return_value=42),
        ):
            with pytest.raises(ValidationError, match="Invalid risk_level"):
                tool(
                    input_text="Test PRD content",
                    category="CORE",
                    priority="P1",
                    risk_level="EXTREMELY_DANGEROUS",
                )

    @pytest.mark.parametrize("risk_level", ["critical", "high", "medium", "low"])
    def test_prd_create_valid_risk_levels_accepted(self, tmp_path: Path, risk_level: str) -> None:
        tool = self._register_and_get("trw_prd_create")
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        with (
            patch("trw_mcp.tools.requirements.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.requirements.next_prd_sequence", return_value=99),
            patch("trw_mcp.tools.requirements.get_config") as mock_get_cfg,
        ):
            mock_get_cfg.return_value.prds_relative_path = "docs/requirements-aare-f/prds"
            mock_get_cfg.return_value.trw_dir = ".trw"
            mock_get_cfg.return_value.index_auto_sync_on_status_change = False
            mock_get_cfg.return_value.ambiguity_rate_max = 0.3
            mock_get_cfg.return_value.completeness_min = 0.7
            mock_get_cfg.return_value.traceability_coverage_min = 0.5
            result = tool(input_text="Test feature", category="CORE", priority="P1", risk_level=risk_level)

        assert result["prd_id"] == "PRD-CORE-099"

    def test_prd_validate_path_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
        tool = self._register_and_get("trw_prd_validate")
        prd_file = tmp_path / "PRD-CORE-001.md"
        prd_file.write_text(
            """\
---
prd:
  id: PRD-CORE-001
  title: Test PRD
  version: '1.0'
  priority: P1
  category: CORE
  status: draft
---

## 1. Problem Statement

This is a test problem statement with real content.

## 2. Goals & Non-Goals

Goals: achieve something meaningful.

## 3. User Stories

As a user I want this feature.

## 4. Functional Requirements

FR01: The system shall do something.

## 5. Non-Functional Requirements

NFR01: Performance target under 100ms.

## 6. Technical Approach

Use Python and pytest.

## 7. Test Strategy

Unit tests and integration tests.

## 8. Rollout Plan

Gradual rollout.

## 9. Success Metrics

Metric: adoption rate.

## 10. Dependencies & Risks

No dependencies.

## 11. Open Questions

None.

## 12. Traceability Matrix

| Requirement | Implementation |
|-------------|----------------|
| FR01 | test.py |
""",
            encoding="utf-8",
        )

        result = tool(prd_path=str(prd_file))
        assert result["path"] == str(prd_file)
        assert "total_score" in result
        assert "quality_tier" in result

    def test_auto_sync_index_failure_returns_false(self, tmp_path: Path) -> None:
        from trw_mcp.tools.requirements import _auto_sync_index

        with patch("trw_mcp.tools.requirements.resolve_project_root", side_effect=RuntimeError("no project root")):
            result = _auto_sync_index()

        assert result is False


class TestRequirementsTemplateNoFrontmatter:
    """Line 327: _load_template_body else branch when no --- frontmatter found."""

    def test_load_template_body_no_frontmatter_uses_raw_body(self) -> None:
        import trw_mcp.tools._prd_template_helpers as helpers
        from trw_mcp.tools._prd_template_helpers import reset_template_cache
        from trw_mcp.tools.requirements import _FRONTMATTER_RE

        original_body = helpers._CACHED_TEMPLATE_BODY
        original_version = helpers._CACHED_TEMPLATE_VERSION
        try:
            reset_template_cache()
            no_frontmatter_content = "# PRD Template\n\n## 1. Problem Statement\n\nContent here.\n"
            assert _FRONTMATTER_RE.match(no_frontmatter_content) is None

            with patch("trw_mcp.tools._prd_template_helpers._load_template_body", wraps=helpers._load_template_body):
                original_rt = Path.read_text

                def fake_read_text(self: Path, *args, **kwargs) -> str:
                    if self.name == "prd_template.md":
                        return no_frontmatter_content
                    return original_rt(self, *args, **kwargs)

                with (
                    patch.object(Path, "read_text", fake_read_text),
                    patch.object(
                        Path,
                        "exists",
                        lambda self: True if self.name == "prd_template.md" else Path.exists(self),
                    ),
                ):
                    reset_template_cache()
                    from trw_mcp.tools.requirements import _load_template_body

                    body = _load_template_body()

            assert "Problem Statement" in body
            assert body == no_frontmatter_content
        finally:
            helpers._CACHED_TEMPLATE_BODY = original_body
            helpers._CACHED_TEMPLATE_VERSION = original_version


class TestRequirementsValidateMissingFile:
    """trw_prd_validate raises StateError when file doesn't exist."""

    def test_prd_validate_missing_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
        server = _make_server()
        register_requirements_tools(server)
        tool = _extract_tool(server, "trw_prd_validate")

        with pytest.raises(StateError, match="PRD file not found"):
            tool(prd_path=str(tmp_path / "NONEXISTENT-PRD.md"))
