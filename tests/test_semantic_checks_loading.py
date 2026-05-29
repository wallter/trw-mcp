"""Tests for semantic review automation loading and language detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.semantic_checks import (
    _get_language_for_file,
    load_semantic_checks,
)


class TestLoadSemanticChecks:
    """FR-1: YAML-based rubric definition."""

    def test_loads_bundled_rubric(self) -> None:
        checks = load_semantic_checks()
        assert len(checks) > 0
        check_ids = {c.id for c in checks}
        assert "dead-hasattr" in check_ids
        assert "bare-except" in check_ids

    def test_loads_from_custom_path(self, tmp_path: Path) -> None:
        rubric = tmp_path / "checks.yaml"
        rubric.write_text(
            "checks:\n"
            "  - id: test-check\n"
            "    description: Test\n"
            "    severity: info\n"
            "    automated: true\n"
            "    pattern: 'test_pattern'\n"
        )
        checks = load_semantic_checks(rubric)
        assert len(checks) == 1
        assert checks[0].id == "test-check"

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        checks = load_semantic_checks(tmp_path / "nonexistent.yaml")
        assert checks == []

    def test_separates_automated_and_manual_checks(self) -> None:
        checks = load_semantic_checks()
        auto = [c for c in checks if c.automated]
        manual = [c for c in checks if not c.automated]
        assert len(auto) > 0
        assert len(manual) > 0

    def test_handles_malformed_yaml(self, tmp_path: Path) -> None:
        rubric = tmp_path / "bad.yaml"
        rubric.write_text("{{{{invalid yaml")
        checks = load_semantic_checks(rubric)
        assert checks == []

    def test_handles_yaml_without_checks_key(self, tmp_path: Path) -> None:
        rubric = tmp_path / "empty.yaml"
        rubric.write_text("other_key: value\n")
        checks = load_semantic_checks(rubric)
        assert checks == []


class TestGetLanguageForFile:
    """Language detection from file extension."""

    def test_python(self) -> None:
        assert _get_language_for_file("foo.py") == "python"

    def test_typescript(self) -> None:
        assert _get_language_for_file("component.tsx") == "typescript"
        assert _get_language_for_file("util.ts") == "typescript"

    def test_javascript(self) -> None:
        assert _get_language_for_file("script.js") == "typescript"
        assert _get_language_for_file("app.jsx") == "typescript"

    def test_go(self) -> None:
        assert _get_language_for_file("main.go") == "go"

    def test_unknown(self) -> None:
        assert _get_language_for_file("Makefile") == "any"
        assert _get_language_for_file("styles.css") == "any"


class TestLoadSemanticChecksEdgeCases:
    """Edge cases for YAML rubric loading."""

    def test_yaml_data_is_not_dict(self, tmp_path: Path) -> None:
        """YAML that parses to a list instead of a dict should return empty."""
        rubric = tmp_path / "list.yaml"
        rubric.write_text("- one\n- two\n")
        checks = load_semantic_checks(rubric)
        assert checks == []

    def test_yaml_data_is_scalar(self, tmp_path: Path) -> None:
        """YAML that parses to a scalar should return empty."""
        rubric = tmp_path / "scalar.yaml"
        rubric.write_text("42\n")
        checks = load_semantic_checks(rubric)
        assert checks == []

    def test_non_dict_items_in_checks_list_skipped(self, tmp_path: Path) -> None:
        """Non-dict items inside the checks list are silently skipped."""
        rubric = tmp_path / "mixed.yaml"
        rubric.write_text(
            "checks:\n"
            "  - just a string\n"
            "  - id: valid-check\n"
            "    description: Good check\n"
            "    severity: info\n"
            "    automated: false\n"
        )
        checks = load_semantic_checks(rubric)
        assert len(checks) == 1
        assert checks[0].id == "valid-check"

    def test_missing_fields_use_defaults(self, tmp_path: Path) -> None:
        """Checks with missing optional fields get sensible defaults."""
        rubric = tmp_path / "minimal.yaml"
        rubric.write_text(
            "checks:\n  - id: minimal\n    description: Minimal check\n    severity: warning\n    automated: true\n"
        )
        checks = load_semantic_checks(rubric)
        assert len(checks) == 1
        assert checks[0].pattern is None
        assert checks[0].language == "any"

    def test_empty_checks_list(self, tmp_path: Path) -> None:
        """An empty checks list returns empty result."""
        rubric = tmp_path / "empty_list.yaml"
        rubric.write_text("checks: []\n")
        checks = load_semantic_checks(rubric)
        assert checks == []

    def test_check_with_all_fields(self, tmp_path: Path) -> None:
        """All fields are correctly populated from YAML."""
        rubric = tmp_path / "full.yaml"
        rubric.write_text(
            "checks:\n"
            "  - id: full-check\n"
            "    description: Full description\n"
            "    severity: error\n"
            "    automated: true\n"
            "    pattern: 'some_pattern'\n"
            "    language: go\n"
        )
        checks = load_semantic_checks(rubric)
        assert len(checks) == 1
        check = checks[0]
        assert check.id == "full-check"
        assert check.description == "Full description"
        assert check.severity == "error"
        assert check.automated is True
        assert check.pattern == "some_pattern"
        assert check.language == "go"

    def test_yaml_load_exception_returns_empty(self, tmp_path: Path) -> None:
        """If YAML().load() raises, returns empty (fail-open)."""
        rubric = tmp_path / "valid.yaml"
        rubric.write_text("checks:\n  - id: x\n    description: x\n    severity: info\n    automated: false\n")
        mock_yaml_cls = MagicMock()
        mock_yaml_cls.return_value.load.side_effect = RuntimeError("parse boom")
        with patch("ruamel.yaml.YAML", mock_yaml_cls):
            checks = load_semantic_checks(rubric)
        assert checks == []


class TestGetLanguageForFileEdgeCases:
    """Edge cases for file extension language detection."""

    def test_path_with_multiple_dots(self) -> None:
        """Only the final extension matters."""
        assert _get_language_for_file("my.module.test.py") == "python"

    def test_no_extension(self) -> None:
        assert _get_language_for_file("Makefile") == "any"

    def test_dotfile(self) -> None:
        assert _get_language_for_file(".gitignore") == "any"

    def test_path_with_directory(self) -> None:
        """Full paths should still detect the extension."""
        assert _get_language_for_file("/home/user/project/app.ts") == "typescript"
        assert _get_language_for_file("src/trw_mcp/tools.py") == "python"
