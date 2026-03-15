"""Tests for MCP prompts — AARE-F prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_prompts_sync


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_prompts() -> dict[str, Any]:
    """Create server and return prompt map."""
    from fastmcp import FastMCP

    from trw_mcp.prompts.aaref import register_aaref_prompts

    srv = FastMCP("test")
    register_aaref_prompts(srv)
    return get_prompts_sync(srv)


class TestElicitPrompt:
    """Tests for elicit prompt."""

    def test_returns_content(self) -> None:
        prompts = _get_prompts()
        result = prompts["elicit"].fn(
            source_type="documentation",
            content="We need a login page",
        )
        assert "documentation" in result
        assert "login page" in result

    def test_code_source(self) -> None:
        prompts = _get_prompts()
        result = prompts["elicit"].fn(
            source_type="code",
            content="def authenticate(user): pass",
        )
        assert "code" in result


class TestPrdCreatePrompt:
    """Tests for prd_create prompt."""

    def test_returns_template(self) -> None:
        prompts = _get_prompts()
        result = prompts["prd_create"].fn(
            requirements="Add caching",
            category="INFRA",
            project_name="Test Project",
        )
        assert "INFRA" in result
        assert "Test Project" in result
        assert "Add caching" in result


class TestValidateQualityPrompt:
    """Tests for validate_quality prompt."""

    def test_returns_validation_prompt(self) -> None:
        prompts = _get_prompts()
        result = prompts["validate_quality"].fn(prd_content="# PRD-CORE-001: Test\n## 1. Problem Statement\n")
        assert "PRD-CORE-001" in result


class TestResolveConflictsPrompt:
    """Tests for resolve_conflicts prompt."""

    def test_returns_resolution_prompt(self) -> None:
        prompts = _get_prompts()
        result = prompts["resolve_conflicts"].fn(
            requirements="FR-001 vs FR-002",
            conflict_description="Performance vs security trade-off",
        )
        assert "FR-001" in result
        assert "Performance vs security" in result


class TestCheckTraceabilityPrompt:
    """Tests for check_traceability prompt."""

    def test_returns_traceability_prompt(self) -> None:
        prompts = _get_prompts()
        result = prompts["check_traceability"].fn(
            requirements="FR-001: Login feature",
            implementation_refs="auth.py:login_handler",
        )
        assert "FR-001" in result
        assert "auth.py" in result
