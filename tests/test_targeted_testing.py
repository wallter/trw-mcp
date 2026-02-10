"""Tests for targeted testing tool (PRD-QUAL-006).

Tests cover: generate_test_map, _resolve_test_file_names,
_extract_imports, resolve_targeted_tests, get_phase_strategy,
TestType, TestDependencyMap, TestResolution, PHASE_TEST_STRATEGIES.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.testing import (
    PHASE_TEST_STRATEGIES,
    TestDependencyMap,
    TestMapping,
    TestResolution,
    TestStrategy,
    TestType,
)
from trw_mcp.tools.testing import (
    _extract_imports,
    _resolve_test_file_names,
    generate_test_map,
    get_phase_strategy,
    resolve_targeted_tests,
)


# --- Models ---


class TestTestModels:
    """Tests for testing models."""

    def test_test_type_values(self) -> None:
        assert TestType.UNIT.value == "unit"
        assert TestType.INTEGRATION.value == "integration"
        assert TestType.E2E.value == "e2e"

    def test_test_mapping_defaults(self) -> None:
        m = TestMapping()
        assert m.tests == []
        assert m.imports == []

    def test_test_dependency_map_defaults(self) -> None:
        m = TestDependencyMap()
        assert m.version == 1
        assert m.mappings == {}

    def test_test_resolution_defaults(self) -> None:
        r = TestResolution()
        assert r.changed_files == []
        assert r.targeted_tests == []
        assert r.fallback_used is False

    def test_test_strategy_defaults(self) -> None:
        s = TestStrategy()
        assert s.phase == ""
        assert s.run_full_suite is False


# --- _resolve_test_file_names ---


class TestResolveTestFileNames:
    """Tests for _resolve_test_file_names."""

    def test_top_level_module(self) -> None:
        result = _resolve_test_file_names(Path("scoring.py"))
        assert result == ["test_scoring.py"]

    def test_nested_module(self) -> None:
        result = _resolve_test_file_names(Path("tools/velocity.py"))
        assert "test_tools_velocity.py" in result
        assert "test_tools.py" in result

    def test_deeply_nested(self) -> None:
        result = _resolve_test_file_names(Path("state/sub/deep.py"))
        assert result[0] == "test_state_sub_deep.py"
        assert result[1] == "test_state_sub.py"


# --- _extract_imports ---


class TestExtractImports:
    """Tests for _extract_imports."""

    def test_from_import(self, tmp_path: Path) -> None:
        py_file = tmp_path / "foo.py"
        py_file.write_text(
            "from trw_mcp.models.config import TRWConfig\n",
            encoding="utf-8",
        )
        result = _extract_imports(py_file, "trw_mcp")
        assert "trw_mcp/models/config.py" in result

    def test_plain_import(self, tmp_path: Path) -> None:
        py_file = tmp_path / "bar.py"
        py_file.write_text(
            "import trw_mcp.scoring\n",
            encoding="utf-8",
        )
        result = _extract_imports(py_file, "trw_mcp")
        assert "trw_mcp/scoring.py" in result

    def test_external_import_ignored(self, tmp_path: Path) -> None:
        py_file = tmp_path / "baz.py"
        py_file.write_text(
            "import json\nfrom pathlib import Path\n",
            encoding="utf-8",
        )
        result = _extract_imports(py_file, "trw_mcp")
        assert result == []

    def test_syntax_error_handled(self, tmp_path: Path) -> None:
        py_file = tmp_path / "bad.py"
        py_file.write_text("def f(:\n", encoding="utf-8")
        result = _extract_imports(py_file, "trw_mcp")
        assert result == []


# --- generate_test_map ---


class TestGenerateTestMap:
    """Tests for generate_test_map."""

    def test_nonexistent_src_dir(self, tmp_path: Path) -> None:
        result = generate_test_map(
            tmp_path / "src", tmp_path / "tests", "trw_mcp",
        )
        assert result.mappings == {}

    def test_basic_mapping(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "scoring.py").write_text(
            "x = 1\n", encoding="utf-8",
        )
        (src_dir / "__init__.py").write_text("", encoding="utf-8")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_scoring.py").write_text(
            "def test_x(): pass\n", encoding="utf-8",
        )

        result = generate_test_map(src_dir, tests_dir, "trw_mcp")
        assert "trw_mcp/scoring.py" in result.mappings
        assert "tests/test_scoring.py" in result.mappings["trw_mcp/scoring.py"].tests

    def test_nested_mapping(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "velocity.py").write_text(
            "v = 1\n", encoding="utf-8",
        )

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_tools_velocity.py").write_text(
            "def test_v(): pass\n", encoding="utf-8",
        )

        result = generate_test_map(src_dir, tests_dir, "trw_mcp")
        assert "trw_mcp/tools/velocity.py" in result.mappings
        mapping = result.mappings["trw_mcp/tools/velocity.py"]
        assert "tests/test_tools_velocity.py" in mapping.tests

    def test_init_py_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("", encoding="utf-8")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        result = generate_test_map(src_dir, tests_dir, "trw_mcp")
        assert not any("__init__" in k for k in result.mappings)


# --- resolve_targeted_tests ---


class TestResolveTargetedTests:
    """Tests for resolve_targeted_tests (BFS transitive resolution)."""

    def _make_map(self) -> TestDependencyMap:
        return TestDependencyMap(
            mappings={
                "trw_mcp/scoring.py": TestMapping(
                    tests=["tests/test_scoring.py"],
                    imports=["trw_mcp/models/config.py"],
                ),
                "trw_mcp/models/config.py": TestMapping(
                    tests=["tests/test_models.py"],
                    imports=[],
                ),
                "trw_mcp/tools/velocity.py": TestMapping(
                    tests=["tests/test_tools_velocity.py"],
                    imports=["trw_mcp/scoring.py", "trw_mcp/models/config.py"],
                ),
            },
        )

    def test_no_changed_files(self) -> None:
        result = resolve_targeted_tests([], TestDependencyMap())
        assert "No changed files" in result.warnings[0]

    def test_empty_map_fallback(self) -> None:
        result = resolve_targeted_tests(
            ["trw_mcp/scoring.py"], TestDependencyMap(),
        )
        assert result.fallback_used is True

    def test_direct_mapping(self) -> None:
        test_map = self._make_map()
        result = resolve_targeted_tests(
            ["trw_mcp/scoring.py"], test_map,
        )
        assert "tests/test_scoring.py" in result.targeted_tests

    def test_transitive_dependency(self) -> None:
        """Changing config.py should trigger tests for scoring.py and velocity.py too."""
        test_map = self._make_map()
        result = resolve_targeted_tests(
            ["trw_mcp/models/config.py"], test_map,
        )
        assert "tests/test_models.py" in result.targeted_tests
        assert "tests/test_scoring.py" in result.targeted_tests
        assert "tests/test_tools_velocity.py" in result.targeted_tests

    def test_unmapped_file(self) -> None:
        test_map = self._make_map()
        result = resolve_targeted_tests(
            ["trw_mcp/unknown.py"], test_map,
        )
        assert "trw_mcp/unknown.py" in result.untested_files

    def test_stale_entries_detected(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()  # Dir exists but test file does not → stale
        test_map = TestDependencyMap(
            mappings={
                "trw_mcp/foo.py": TestMapping(
                    tests=["tests/test_foo.py"],
                    imports=[],
                ),
            },
        )
        result = resolve_targeted_tests(
            ["trw_mcp/foo.py"], test_map, tests_dir,
        )
        assert "tests/test_foo.py" in result.stale_entries
        assert "tests/test_foo.py" not in result.targeted_tests


# --- get_phase_strategy ---


class TestGetPhaseStrategy:
    """Tests for get_phase_strategy."""

    def test_implement_strategy(self) -> None:
        s = get_phase_strategy("implement")
        assert s.phase == "implement"
        assert "unit" in s.recommended_markers
        assert s.run_full_suite is False

    def test_validate_strategy(self) -> None:
        s = get_phase_strategy("validate")
        assert s.run_coverage is True
        assert s.run_mypy is True

    def test_deliver_strategy(self) -> None:
        s = get_phase_strategy("deliver")
        assert s.run_full_suite is True
        assert "e2e" in s.recommended_markers

    def test_research_no_tests(self) -> None:
        s = get_phase_strategy("research")
        assert s.recommended_markers == []
        assert s.run_full_suite is False

    def test_unknown_phase_defaults_to_implement(self) -> None:
        s = get_phase_strategy("nonexistent")
        assert s.phase == "implement"

    def test_case_insensitive(self) -> None:
        s = get_phase_strategy("DELIVER")
        assert s.run_full_suite is True


# --- PHASE_TEST_STRATEGIES ---


class TestPhaseTestStrategies:
    """Tests for the PHASE_TEST_STRATEGIES mapping."""

    def test_all_phases_covered(self) -> None:
        expected = {"research", "plan", "implement", "validate", "review", "deliver"}
        assert set(PHASE_TEST_STRATEGIES.keys()) == expected

    def test_each_strategy_has_phase_field(self) -> None:
        for phase, strategy in PHASE_TEST_STRATEGIES.items():
            assert strategy.phase == phase

    def test_deliver_is_most_comprehensive(self) -> None:
        deliver = PHASE_TEST_STRATEGIES["deliver"]
        assert deliver.run_full_suite is True
        assert deliver.run_coverage is True
        assert deliver.run_mypy is True
        assert len(deliver.recommended_markers) >= 3
