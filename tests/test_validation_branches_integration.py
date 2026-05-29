"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from trw_mcp.state.validation import check_integration


class TestCheckIntegrationEdgeCases:
    """Additional edge cases for check_integration."""

    def test_server_py_missing_does_not_crash(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "mytool.py").write_text(
            "def register_mytool_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir(parents=True)
        result = check_integration(src_dir)
        assert "mytool" in result["unregistered"]

    def test_tool_with_call_site_but_no_import_is_registered(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        (tools_dir / "custom.py").write_text(
            "def register_custom_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (src_dir / "server.py").write_text(
            "register_custom_tools(server)\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert "custom" not in result["unregistered"]
        assert result["all_registered"] is True

    def test_tool_file_read_error_is_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir(parents=True)

        tool_file = tools_dir / "unreadable.py"
        tool_file.write_text("def register_unreadable_tools(server):\n    pass\n")

        with patch("builtins.open", side_effect=OSError("permission denied")):
            pass

        original_read_text = Path.read_text

        def patched_read_text(self: Path, **kwargs: Any) -> str:
            if self.name == "unreadable.py":
                raise OSError("permission denied")
            return original_read_text(self, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = check_integration(src_dir)
        assert "unreadable" not in result["unregistered"]

    def test_returns_conventions_key(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert "conventions" in result
        assert "tool_pattern" in result["conventions"]
        assert "test_pattern" in result["conventions"]

    def test_tool_modules_scanned_count(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir(parents=True)

        (tools_dir / "tool_a.py").write_text(
            "def register_tool_a_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tools_dir / "tool_b.py").write_text(
            "def register_tool_b_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tools_dir / "helper.py").write_text(
            "def some_helper():\n    pass\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert result["tool_modules_scanned"] == 2

    def test_init_file_is_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tools_dir / "__init__.py").write_text(
            "def register_init_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert "__init__" not in result["unregistered"]
        assert result["tool_modules_scanned"] == 0

    def test_alternate_test_name_satisfies_missing_check(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "widget.py").write_text(
            "def register_widget_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tests_dir / "test_widget.py").write_text("# tests\n", encoding="utf-8")

        result = check_integration(src_dir)
        assert "test_tools_widget.py" not in result["missing_tests"]
