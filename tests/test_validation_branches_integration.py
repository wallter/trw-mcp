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
        tool_file.write_text("def register_unreadable_tools(server):\n    return None\n")

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


class TestCheckIntegrationServerPackageTopology:
    """Registration evidence lives in a ``server/`` package, not ``server.py``.

    This locks in the fix for the false-positive regression where the check
    assumed a single ``server.py`` module. Production trw-mcp wires tools in
    ``server/_tools.py``; with the old logic every registered tool module was
    reported as unregistered.
    """

    def test_tool_wired_in_server_package_is_registered(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "src" / "tests").mkdir(parents=True, exist_ok=True)

        (tools_dir / "widget.py").write_text(
            "def register_widget_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        # No server.py — registration lives in the server/ package, matching
        # the real trw-mcp topology (server/_tools.py::_register_tools).
        server_pkg = src_dir / "server"
        server_pkg.mkdir()
        (server_pkg / "__init__.py").write_text("", encoding="utf-8")
        (server_pkg / "_tools.py").write_text(
            "from trw_mcp.tools.widget import register_widget_tools\n"
            "register_widget_tools(mcp)\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        assert "widget" not in result["unregistered"]
        assert result["all_registered"] is True

    def test_no_server_module_or_package_flags_unregistered(self, tmp_path: Path) -> None:
        # Neither server.py nor a server/ package: genuinely unwired → flagged.
        src_dir = tmp_path / "src" / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "widget.py").write_text(
            "def register_widget_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert "widget" in result["unregistered"]
        assert result["all_registered"] is False


class TestCheckIntegrationRealTopology:
    """Guard against false positives on the installed trw_mcp package itself."""

    def test_installed_package_reports_no_unregistered_tools(self) -> None:
        import trw_mcp

        src_dir = Path(trw_mcp.__file__).resolve().parent
        result = check_integration(src_dir)

        # Every tool module wired in server/_tools.py must be seen as registered.
        # Pre-fix, this returned all 23 tool modules as false positives because
        # the check looked for a non-existent src/trw_mcp/server.py.
        assert isinstance(result["tool_modules_scanned"], int)
        assert result["tool_modules_scanned"] > 0, "expected at least one tool module"
        assert result["unregistered"] == [], (
            f"false-positive unregistered tool modules: {result['unregistered']}"
        )
        assert result["all_registered"] is True
