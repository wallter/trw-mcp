"""Tests for PRD-QUAL-011: Integration Validation at Phase Gates.

Covers:
- check_integration tool registration scanner (FR01)
- Test file coverage check (FR02)
- Convention documentation (FR04)
- Edge cases (missing server.py, no register function, private modules)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.validation import check_integration


# --- Fixtures ---


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """Create a mock source directory with tools/ and server.py."""
    src = tmp_path / "src" / "trw_mcp"
    tools = src / "tools"
    tools.mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()

    # Create tool modules
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "orchestration.py").write_text(
        "def register_orchestration_tools(server):\n    pass\n",
        encoding="utf-8",
    )
    (tools / "learning.py").write_text(
        "def register_learning_tools(server):\n    pass\n",
        encoding="utf-8",
    )
    (tools / "requirements.py").write_text(
        "def register_requirements_tools(server):\n    pass\n",
        encoding="utf-8",
    )

    # Create server.py with registrations for 2 out of 3
    (src / "server.py").write_text(
        "from trw_mcp.tools.orchestration import register_orchestration_tools\n"
        "from trw_mcp.tools.learning import register_learning_tools\n"
        "\n"
        "register_orchestration_tools(mcp)\n"
        "register_learning_tools(mcp)\n",
        encoding="utf-8",
    )

    # Create test files for only orchestration
    (tests / "test_tools_orchestration.py").write_text("# tests\n", encoding="utf-8")

    return src


# --- check_integration ---


class TestCheckIntegration:
    """PRD-QUAL-011-FR01: Tool registration scanner."""

    def test_detects_unregistered_module(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        assert "requirements" in result["unregistered"]

    def test_registered_modules_not_flagged(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        assert "orchestration" not in result["unregistered"]
        assert "learning" not in result["unregistered"]

    def test_all_registered_flag(self, source_dir: Path) -> None:
        # Not all registered (requirements is missing)
        result = check_integration(source_dir)
        assert result["all_registered"] is False

    def test_all_registered_when_complete(self, source_dir: Path) -> None:
        # Add the missing registration
        server_path = source_dir / "server.py"
        content = server_path.read_text(encoding="utf-8")
        content += (
            "from trw_mcp.tools.requirements import register_requirements_tools\n"
            "register_requirements_tools(mcp)\n"
        )
        server_path.write_text(content, encoding="utf-8")
        result = check_integration(source_dir)
        assert result["all_registered"] is True

    def test_tool_modules_scanned_count(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        # 3 modules (orchestration, learning, requirements), __init__ excluded
        assert result["tool_modules_scanned"] == 3

    def test_excludes_init_and_private_modules(self, source_dir: Path) -> None:
        tools = source_dir / "tools"
        (tools / "_helpers.py").write_text(
            "def register_helpers_tools(s): pass\n", encoding="utf-8",
        )
        result = check_integration(source_dir)
        assert "_helpers" not in result["unregistered"]


class TestTestFileCoverage:
    """PRD-QUAL-011-FR02: Test file coverage check."""

    def test_detects_missing_test_files(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        # learning and requirements lack test files
        assert "test_tools_learning.py" in result["missing_tests"]
        assert "test_tools_requirements.py" in result["missing_tests"]

    def test_existing_test_not_flagged(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        assert "test_tools_orchestration.py" not in result["missing_tests"]

    def test_alternative_test_name_accepted(self, source_dir: Path) -> None:
        # tests/test_learning.py (alternative naming) should be accepted
        tests_dir = source_dir.parent.parent / "tests"
        (tests_dir / "test_learning.py").write_text("# tests\n", encoding="utf-8")
        result = check_integration(source_dir)
        assert "test_tools_learning.py" not in result["missing_tests"]


class TestConventions:
    """PRD-QUAL-011-FR04: Convention documentation."""

    def test_conventions_in_result(self, source_dir: Path) -> None:
        result = check_integration(source_dir)
        assert "conventions" in result
        conventions = result["conventions"]
        assert "tool_pattern" in conventions
        assert "test_pattern" in conventions


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_missing_server_py(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "trw_mcp"
        tools = src / "tools"
        tools.mkdir(parents=True)
        (tools / "foo.py").write_text(
            "def register_foo_tools(s): pass\n", encoding="utf-8",
        )
        # No server.py
        result = check_integration(src)
        assert "foo" in result["unregistered"]

    def test_tool_without_register_function(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "trw_mcp"
        tools = src / "tools"
        tools.mkdir(parents=True)
        (src / "server.py").write_text("# empty\n", encoding="utf-8")
        (tools / "utils.py").write_text(
            "def helper(): pass\n", encoding="utf-8",
        )
        result = check_integration(src)
        # Module without register function should not appear in unregistered
        assert "utils" not in result["unregistered"]

    def test_empty_tools_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "trw_mcp"
        tools = src / "tools"
        tools.mkdir(parents=True)
        (src / "server.py").write_text("# empty\n", encoding="utf-8")
        result = check_integration(src)
        assert result["unregistered"] == []
        assert result["all_registered"] is True

    def test_nonexistent_tools_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "trw_mcp"
        src.mkdir(parents=True)
        (src / "server.py").write_text("# empty\n", encoding="utf-8")
        result = check_integration(src)
        assert result["unregistered"] == []
        assert result["tool_modules_scanned"] == 0
