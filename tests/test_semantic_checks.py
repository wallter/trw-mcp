"""Tests for semantic review automation (PRD-QUAL-040)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.semantic_checks import (
    SemanticCheck,
    SemanticCheckResult,
    SemanticFinding,
    _get_language_for_file,
    format_semantic_report,
    load_semantic_checks,
    run_semantic_checks,
)


class TestLoadSemanticChecks:
    """FR-1: YAML-based rubric definition."""

    def test_loads_bundled_rubric(self) -> None:
        checks = load_semantic_checks()
        assert len(checks) > 0
        # Should have at least the core checks
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


class TestRunSemanticChecks:
    """FR-2: Automated pattern checks."""

    def test_detects_bare_except(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("try:\n    x = 1\nexcept:\n    pass\n")

        result = run_semantic_checks([str(f)])
        assert result.files_scanned == 1
        bare_excepts = [r for r in result.findings if r.check_id == "bare-except"]
        assert len(bare_excepts) >= 1

    def test_detects_hasattr(self, tmp_path: Path) -> None:
        f = tmp_path / "models.py"
        f.write_text('if hasattr(user, "email"):\n    pass\n')

        result = run_semantic_checks([str(f)])
        hasattr_findings = [r for r in result.findings if r.check_id == "dead-hasattr"]
        assert len(hasattr_findings) >= 1

    def test_detects_print_statements(self, tmp_path: Path) -> None:
        f = tmp_path / "debug.py"
        f.write_text('print("debug output")\n')

        result = run_semantic_checks([str(f)])
        prints = [r for r in result.findings if r.check_id == "print-debug"]
        assert len(prints) >= 1

    def test_detects_todo_comments(self, tmp_path: Path) -> None:
        f = tmp_path / "wip.py"
        f.write_text("# TODO: fix this\nx = 1\n")

        result = run_semantic_checks([str(f)])
        todos = [r for r in result.findings if r.check_id == "todo-fixme"]
        assert len(todos) >= 1

    def test_no_findings_in_clean_code(self, tmp_path: Path) -> None:
        f = tmp_path / "clean.py"
        f.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

        result = run_semantic_checks([str(f)])
        assert len(result.findings) == 0

    def test_skips_nonexistent_files(self, tmp_path: Path) -> None:
        result = run_semantic_checks([str(tmp_path / "nope.py")])
        assert result.files_scanned == 0

    def test_respects_language_filter(self, tmp_path: Path) -> None:
        """Python-specific checks should not run on .ts files."""
        f = tmp_path / "code.ts"
        f.write_text('print("hello")\n')  # print is Python-only check

        result = run_semantic_checks([str(f)])
        prints = [r for r in result.findings if r.check_id == "print-debug"]
        assert len(prints) == 0  # Should skip — language is typescript, check is python

    def test_custom_checks(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("x = MAGIC_NUMBER\n")

        custom = [
            SemanticCheck(
                id="magic-number",
                description="Magic number detected",
                severity="warning",
                automated=True,
                pattern="MAGIC_NUMBER",
                language="python",
            )
        ]
        result = run_semantic_checks([str(f)], checks=custom)
        assert len(result.findings) == 1
        assert result.findings[0].check_id == "magic-number"

    def test_records_matched_text(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("try:\n    pass\nexcept:\n    pass\n")

        result = run_semantic_checks([str(f)])
        bare = [r for r in result.findings if r.check_id == "bare-except"]
        assert len(bare) >= 1
        assert bare[0].matched_text == "except:"
        assert bare[0].line_number == 3

    def test_handles_unreadable_file(self, tmp_path: Path) -> None:
        """Files that raise OSError on read should be skipped."""
        f = tmp_path / "unreadable.py"
        f.write_text("content")

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            # The file exists but read fails — should be skipped gracefully
            result = run_semantic_checks([str(f)])
        # files_scanned stays 0 because read failed
        assert result.files_scanned == 0

    def test_skips_non_automated_checks(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("status = 'admin'\n")

        manual_only = [
            SemanticCheck(
                id="manual-check",
                description="Manual review only",
                severity="warning",
                automated=False,
                pattern="admin",
                language="python",
            )
        ]
        result = run_semantic_checks([str(f)], checks=manual_only)
        assert result.checks_run == 0
        assert len(result.findings) == 0

    def test_handles_invalid_regex(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("some text\n")

        bad_regex = [
            SemanticCheck(
                id="bad-regex",
                description="Broken regex",
                severity="warning",
                automated=True,
                pattern="[invalid((",
                language="python",
            )
        ]
        result = run_semantic_checks([str(f)], checks=bad_regex)
        # Should not crash, just skip the bad check
        assert len(result.findings) == 0


class TestSemanticCheckResult:
    """Result aggregation."""

    def test_counts_by_severity(self) -> None:
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "error", "f.py", 1, "x"),
                SemanticFinding("b", "d", "warning", "f.py", 2, "y"),
                SemanticFinding("c", "d", "warning", "f.py", 3, "z"),
                SemanticFinding("d", "d", "info", "f.py", 4, "w"),
            ]
        )
        assert result.error_count == 1
        assert result.warning_count == 2
        assert result.info_count == 1

    def test_empty_result(self) -> None:
        result = SemanticCheckResult()
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0


class TestFormatSemanticReport:
    """FR-4: Build report semantic section."""

    def test_no_findings_report(self) -> None:
        result = SemanticCheckResult(checks_run=5, files_scanned=3)
        report = format_semantic_report(result)
        assert "No semantic issues found" in report
        assert "5 checks" in report
        assert "3 files" in report

    def test_findings_grouped_by_severity(self) -> None:
        result = SemanticCheckResult(
            checks_run=5,
            files_scanned=2,
            findings=[
                SemanticFinding("bare-except", "Bare except", "warning", "f.py", 10, "except:"),
                SemanticFinding("hardcoded-secret", "Secret", "error", "g.py", 5, 'key="abc"'),
            ],
        )
        report = format_semantic_report(result)
        assert "ERROR" in report
        assert "WARNING" in report
        assert "bare-except" in report
        assert "hardcoded-secret" in report

    def test_includes_file_location(self) -> None:
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "warning", "app/main.py", 42, "match"),
            ]
        )
        report = format_semantic_report(result)
        assert "app/main.py:42" in report

    def test_report_header(self) -> None:
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=2,
            findings=[
                SemanticFinding("x", "d", "info", "f.py", 1, "m"),
            ],
        )
        report = format_semantic_report(result)
        assert "## Semantic Warnings" in report
        assert "1 issue(s)" in report


class TestBestEffortSemanticCheck:
    """FR-4: Integration with trw_build_check."""

    def test_appends_findings_as_validation_failures(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        mock_result = SemanticCheckResult(
            findings=[
                SemanticFinding("bare-except", "Bare except", "warning", "f.py", 10, "except:"),
            ]
        )

        with patch(
            "trw_mcp.state.semantic_checks.run_semantic_checks",
            return_value=mock_result,
        ) as mock_run:
            with patch("trw_mcp.state._paths.resolve_project_root") as mock_root:
                mock_root.return_value = Path("/project")
                with patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = MagicMock(stdout="app/main.py\n")
                    _best_effort_semantic_check(config, failures)

        mock_run.assert_called_once()
        assert len(failures) == 1
        assert failures[0].severity == "warning"
        assert failures[0].rule == "bare-except"

    def test_skipped_when_disabled(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = False
        failures: list[ValidationFailure] = []

        _best_effort_semantic_check(config, failures)
        assert len(failures) == 0

    def test_skips_info_severity(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        mock_result = SemanticCheckResult(
            findings=[
                SemanticFinding("todo-fixme", "TODO comment", "info", "f.py", 1, "TODO"),
            ]
        )

        with patch(
            "trw_mcp.state.semantic_checks.run_semantic_checks",
            return_value=mock_result,
        ):
            with patch("trw_mcp.state._paths.resolve_project_root") as mock_root:
                mock_root.return_value = Path("/project")
                with patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = MagicMock(stdout="app/main.py\n")
                    _best_effort_semantic_check(config, failures)

        assert len(failures) == 0  # info-level is skipped

    def test_never_raises(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        with patch(
            "trw_mcp.state.semantic_checks.run_semantic_checks",
            side_effect=RuntimeError("boom"),
        ):
            with patch("trw_mcp.state._paths.resolve_project_root") as mock_root:
                mock_root.return_value = Path("/project")
                with patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = MagicMock(stdout="app/main.py\n")
                    # Should not raise
                    _best_effort_semantic_check(config, failures)

        assert len(failures) == 0

    def test_caps_at_10_findings(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        # Create 15 warning findings
        mock_result = SemanticCheckResult(
            findings=[SemanticFinding(f"check-{i}", "desc", "warning", "f.py", i, "match") for i in range(15)]
        )

        with patch(
            "trw_mcp.state.semantic_checks.run_semantic_checks",
            return_value=mock_result,
        ):
            with patch("trw_mcp.state._paths.resolve_project_root") as mock_root:
                mock_root.return_value = Path("/project")
                with patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = MagicMock(stdout="app/main.py\n")
                    _best_effort_semantic_check(config, failures)

        assert len(failures) == 10  # Capped at 10


# ---------------------------------------------------------------------------
# Edge-case and gap-filling tests
# ---------------------------------------------------------------------------


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
        c = checks[0]
        assert c.id == "full-check"
        assert c.description == "Full description"
        assert c.severity == "error"
        assert c.automated is True
        assert c.pattern == "some_pattern"
        assert c.language == "go"

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


class TestRunSemanticChecksEdgeCases:
    """Edge cases for the main check runner."""

    def test_empty_file_paths_list(self) -> None:
        """Empty file list returns zero scanned, zero findings."""
        checks = [
            SemanticCheck(
                id="test",
                description="d",
                severity="warning",
                automated=True,
                pattern="x",
                language="any",
            )
        ]
        result = run_semantic_checks([], checks=checks)
        assert result.files_scanned == 0
        assert result.checks_run == 1
        assert len(result.findings) == 0

    def test_multiple_files_multiple_findings(self, tmp_path: Path) -> None:
        """Findings accumulate across multiple files."""
        f1 = tmp_path / "a.py"
        f1.write_text("try:\n    pass\nexcept:\n    pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("try:\n    pass\nexcept:\n    pass\n")

        checks = [
            SemanticCheck(
                id="bare-except",
                description="Bare except",
                severity="warning",
                automated=True,
                pattern=r"except\s*:",
                language="python",
            )
        ]
        result = run_semantic_checks([str(f1), str(f2)], checks=checks)
        assert result.files_scanned == 2
        assert len(result.findings) == 2

    def test_language_any_matches_all_file_types(self, tmp_path: Path) -> None:
        """A check with language='any' runs against all file types."""
        py_file = tmp_path / "code.py"
        py_file.write_text("# FIXME: broken\n")
        ts_file = tmp_path / "code.ts"
        ts_file.write_text("// FIXME: broken\n")
        go_file = tmp_path / "code.go"
        go_file.write_text("// FIXME: broken\n")

        checks = [
            SemanticCheck(
                id="fixme",
                description="FIXME found",
                severity="info",
                automated=True,
                pattern=r"FIXME",
                language="any",
            )
        ]
        result = run_semantic_checks([str(py_file), str(ts_file), str(go_file)], checks=checks)
        assert result.files_scanned == 3
        assert len(result.findings) == 3

    def test_automated_check_without_pattern_skipped(self, tmp_path: Path) -> None:
        """Automated=True but pattern=None should not produce findings."""
        f = tmp_path / "code.py"
        f.write_text("anything\n")

        checks = [
            SemanticCheck(
                id="no-pattern",
                description="Manual only",
                severity="warning",
                automated=True,
                pattern=None,
                language="any",
            )
        ]
        result = run_semantic_checks([str(f)], checks=checks)
        # Pattern is None so it's filtered out of auto_checks
        assert result.checks_run == 0
        assert len(result.findings) == 0

    def test_language_mismatch_skips_check(self, tmp_path: Path) -> None:
        """A go-specific check does not run on python files."""
        f = tmp_path / "code.py"
        f.write_text("goroutine something\n")

        checks = [
            SemanticCheck(
                id="go-check",
                description="Go pattern",
                severity="warning",
                automated=True,
                pattern=r"goroutine",
                language="go",
            )
        ]
        result = run_semantic_checks([str(f)], checks=checks)
        assert result.files_scanned == 1
        assert len(result.findings) == 0

    def test_multiple_matches_same_file(self, tmp_path: Path) -> None:
        """Multiple matches on different lines in the same file."""
        f = tmp_path / "code.py"
        f.write_text("# TODO: first\nx = 1\n# TODO: second\n")

        checks = [
            SemanticCheck(
                id="todo",
                description="TODO found",
                severity="info",
                automated=True,
                pattern=r"TODO",
                language="any",
            )
        ]
        result = run_semantic_checks([str(f)], checks=checks)
        assert result.files_scanned == 1
        assert len(result.findings) == 2
        assert result.findings[0].line_number == 1
        assert result.findings[1].line_number == 3

    def test_empty_file_no_findings(self, tmp_path: Path) -> None:
        """An empty file produces no findings."""
        f = tmp_path / "empty.py"
        f.write_text("")

        checks = [
            SemanticCheck(
                id="test",
                description="d",
                severity="warning",
                automated=True,
                pattern=r"anything",
                language="any",
            )
        ]
        result = run_semantic_checks([str(f)], checks=checks)
        assert result.files_scanned == 1
        assert len(result.findings) == 0

    def test_loads_rubric_when_checks_not_provided(self, tmp_path: Path) -> None:
        """When checks=None, loads from rubric_path."""
        rubric = tmp_path / "rubric.yaml"
        rubric.write_text(
            "checks:\n"
            "  - id: custom-id\n"
            "    description: Custom check\n"
            "    severity: warning\n"
            "    automated: true\n"
            "    pattern: 'CUSTOM_MARKER'\n"
            "    language: any\n"
        )
        f = tmp_path / "code.py"
        f.write_text("x = CUSTOM_MARKER\n")

        result = run_semantic_checks([str(f)], rubric_path=rubric)
        assert len(result.findings) == 1
        assert result.findings[0].check_id == "custom-id"

    def test_finding_attributes(self, tmp_path: Path) -> None:
        """Verify all SemanticFinding attributes are populated correctly."""
        f = tmp_path / "target.py"
        f.write_text("line_one\nMATCH_HERE\nline_three\n")

        checks = [
            SemanticCheck(
                id="attr-check",
                description="Attribute test",
                severity="error",
                automated=True,
                pattern=r"MATCH_HERE",
                language="python",
            )
        ]
        result = run_semantic_checks([str(f)], checks=checks)
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.check_id == "attr-check"
        assert finding.description == "Attribute test"
        assert finding.severity == "error"
        assert finding.file_path == str(f)
        assert finding.line_number == 2
        assert finding.matched_text == "MATCH_HERE"


class TestSemanticCheckResultEdgeCases:
    """Edge cases for the result dataclass."""

    def test_only_info_findings(self) -> None:
        """Result with only info findings has 0 errors and 0 warnings."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "info", "f.py", 1, "x"),
                SemanticFinding("b", "d", "info", "f.py", 2, "y"),
            ]
        )
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 2

    def test_unknown_severity_not_counted(self) -> None:
        """Findings with an unrecognized severity are not counted by any property."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "critical", "f.py", 1, "x"),
            ]
        )
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0
        # But the finding is still present
        assert len(result.findings) == 1

    def test_default_values(self) -> None:
        """Default construction has zero counts and empty findings."""
        result = SemanticCheckResult()
        assert result.checks_run == 0
        assert result.files_scanned == 0
        assert result.findings == []


class TestFormatSemanticReportEdgeCases:
    """Edge cases for the markdown report formatter."""

    def test_info_only_findings_no_error_warning_sections(self) -> None:
        """Report with only info findings shows INFO section, no ERROR/WARNING."""
        result = SemanticCheckResult(
            checks_run=1,
            files_scanned=1,
            findings=[
                SemanticFinding("todo", "TODO found", "info", "f.py", 1, "TODO"),
            ],
        )
        report = format_semantic_report(result)
        assert "INFO (1)" in report
        assert "ERROR" not in report
        assert "WARNING" not in report

    def test_empty_matched_text_omits_code_block(self) -> None:
        """When matched_text is empty, no inline code block is appended."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "warning", "f.py", 1, ""),
            ],
        )
        report = format_semantic_report(result)
        # The finding line is present
        assert "f.py:1" in report
        # But no empty backtick block follows
        lines = report.split("\n")
        for i, line in enumerate(lines):
            if "f.py:1" in line:
                # Next non-empty line should NOT be just backticks with empty content
                if i + 1 < len(lines) and lines[i + 1].strip():
                    assert lines[i + 1].strip() != "``"
                break

    def test_long_matched_text_truncated_at_80_chars(self) -> None:
        """Matched text longer than 80 chars is truncated in the report."""
        long_text = "A" * 120
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "error", "f.py", 1, long_text),
            ],
        )
        report = format_semantic_report(result)
        # The full 120-char string should NOT appear
        assert long_text not in report
        # But the truncated 80-char version should
        assert "A" * 80 in report

    def test_findings_capped_at_20_per_severity(self, tmp_path: Path) -> None:
        """No more than 20 findings shown per severity group."""
        findings = [SemanticFinding(f"check-{i}", "desc", "warning", "f.py", i, "match") for i in range(30)]
        result = SemanticCheckResult(
            checks_run=1,
            files_scanned=1,
            findings=findings,
        )
        report = format_semantic_report(result)
        # Count how many "check-" entries appear under WARNING
        check_mentions = report.count("**check-")
        assert check_mentions == 20

    def test_severity_ordering_in_report(self) -> None:
        """Errors appear before warnings, which appear before info."""
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=1,
            findings=[
                SemanticFinding("info-1", "d", "info", "f.py", 3, "m"),
                SemanticFinding("warn-1", "d", "warning", "f.py", 2, "m"),
                SemanticFinding("err-1", "d", "error", "f.py", 1, "m"),
            ],
        )
        report = format_semantic_report(result)
        error_pos = report.index("ERROR")
        warning_pos = report.index("WARNING")
        info_pos = report.index("INFO")
        assert error_pos < warning_pos < info_pos

    def test_severity_icons(self) -> None:
        """Each severity gets its correct icon prefix."""
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=1,
            findings=[
                SemanticFinding("e", "d", "error", "f.py", 1, "m"),
                SemanticFinding("w", "d", "warning", "f.py", 2, "m"),
                SemanticFinding("i", "d", "info", "f.py", 3, "m"),
            ],
        )
        report = format_semantic_report(result)
        assert "[!!!]" in report  # error icon
        assert "[!!]" in report  # warning icon
        assert "[i]" in report  # info icon

    def test_report_ends_with_newline(self) -> None:
        """Both empty and non-empty reports end with a newline."""
        empty = format_semantic_report(SemanticCheckResult(checks_run=1))
        assert empty.endswith("\n")

        non_empty = format_semantic_report(
            SemanticCheckResult(
                findings=[
                    SemanticFinding("x", "d", "warning", "f.py", 1, "m"),
                ],
            )
        )
        assert non_empty.endswith("\n")


class TestSemanticCheckDataclass:
    """SemanticCheck dataclass field defaults."""

    def test_defaults(self) -> None:
        check = SemanticCheck(
            id="test",
            description="desc",
            severity="info",
            automated=False,
        )
        assert check.pattern is None
        assert check.language == "any"

    def test_all_fields_set(self) -> None:
        check = SemanticCheck(
            id="test",
            description="desc",
            severity="error",
            automated=True,
            pattern=r"\bfoo\b",
            language="go",
        )
        assert check.id == "test"
        assert check.pattern == r"\bfoo\b"
        assert check.language == "go"


class TestSemanticFindingDataclass:
    """SemanticFinding field access."""

    def test_attributes(self) -> None:
        f = SemanticFinding(
            check_id="bare-except",
            description="Bare except clause",
            severity="warning",
            file_path="app/main.py",
            line_number=42,
            matched_text="except:",
        )
        assert f.check_id == "bare-except"
        assert f.description == "Bare except clause"
        assert f.severity == "warning"
        assert f.file_path == "app/main.py"
        assert f.line_number == 42
        assert f.matched_text == "except:"
