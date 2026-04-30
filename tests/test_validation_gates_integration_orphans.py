"""Coverage tests for integration and orphan module validation helpers."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation import check_integration
from trw_mcp.state.validation.integration_check import check_orphan_modules


class TestCheckIntegrationEmptyToolsDir:
    """check_integration handles empty or absent tools directory."""

    def test_empty_tools_dir_returns_empty_lists(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "mypackage"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("# empty server\n", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["unregistered"] == []
        assert result["all_registered"] is True
        assert result["tool_modules_scanned"] == 0

    def test_absent_tools_dir_returns_empty(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "mypackage"
        src_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("# empty\n", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["unregistered"] == []


class TestCheckIntegrationUnregisteredModule:
    """Module with register_*_tools not wired into server.py is unregistered."""

    def test_unregistered_tool_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tools_dir / "foo.py").write_text(
            "def register_foo_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (src_dir / "server.py").write_text(
            "# no imports for foo\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "foo" in unreg
        assert result["all_registered"] is False

    def test_registered_tool_not_in_unregistered(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        (tools_dir / "bar.py").write_text(
            "def register_bar_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (src_dir / "server.py").write_text(
            "from pkg.tools.bar import register_bar_tools\nregister_bar_tools(server)\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "bar" not in unreg
        assert result["all_registered"] is True


class TestCheckIntegrationMissingTests:
    """Modules without corresponding test files appear in missing_tests."""

    def test_missing_test_file_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "baz.py").write_text(
            "def register_baz_tools(server):\n    pass\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        missing = result["missing_tests"]
        assert isinstance(missing, list)
        assert "test_tools_baz.py" in missing

    def test_present_test_file_not_in_missing(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "qux.py").write_text(
            "def register_qux_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tests_dir / "test_tools_qux.py").write_text(
            "# tests\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        missing = result["missing_tests"]
        assert isinstance(missing, list)
        assert "test_tools_qux.py" not in missing

    def test_conventions_key_present(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert "conventions" in result
        conventions = result["conventions"]
        assert isinstance(conventions, dict)
        assert "tool_pattern" in conventions


class TestCheckIntegrationAllRegistered:
    """When all tool modules are registered, all_registered is True."""

    def test_all_registered_true_when_no_tools(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["all_registered"] is True

    def test_private_modules_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tools_dir / "_private.py").write_text(
            "def register_private_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "_private" not in unreg
        assert result["tool_modules_scanned"] == 0


class TestCheckOrphanModulesNoOrphans:
    """Modules imported by at least one other file are not orphans."""

    def test_imported_module_not_orphan(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text(
            "from pkg.state.foo import helper\n",
            encoding="utf-8",
        )
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "foo.py").write_text(
            "def helper(): pass\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        assert result["all_reachable"] is True
        assert result["orphans"] == []
        assert result["modules_scanned"] >= 1

    def test_relative_import_counts(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text(
            "from .bar import something\n",
            encoding="utf-8",
        )
        (state_dir / "bar.py").write_text(
            "something = 1\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        assert "state/bar.py" not in result["orphans"]


class TestCheckOrphanModulesDetectsOrphans:
    """Modules not imported by any other source file are orphans."""

    def test_orphan_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("# no imports\n", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "dead_module.py").write_text(
            "def unreachable(): pass\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        orphans = result["orphans"]
        assert isinstance(orphans, list)
        assert "state/dead_module.py" in orphans
        assert result["all_reachable"] is False

    def test_multiple_orphans_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        models_dir = src_dir / "models"
        state_dir.mkdir(parents=True)
        models_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("# no imports\n", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "orphan_a.py").write_text("x = 1\n", encoding="utf-8")
        (models_dir / "__init__.py").write_text("", encoding="utf-8")
        (models_dir / "orphan_b.py").write_text("y = 2\n", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        orphans = result["orphans"]
        assert "state/orphan_a.py" in orphans
        assert "models/orphan_b.py" in orphans


class TestCheckOrphanModulesExclusions:
    """__init__.py and entry points are excluded from orphan scanning."""

    def test_init_py_excluded(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        assert result["modules_scanned"] == 0

    def test_entry_points_excluded(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (src_dir / "__main__.py").write_text("", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        orphans = result.get("orphans", [])
        assert isinstance(orphans, list)
        assert "server.py" not in orphans
        assert "__main__.py" not in orphans

    def test_package_import_from_dot_counts(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text(
            "from . import baz\n",
            encoding="utf-8",
        )
        (state_dir / "baz.py").write_text("z = 1\n", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        assert "state/baz.py" not in result["orphans"]
