"""Tests for semantic review automation (PRD-QUAL-040)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
                SemanticFinding(
                    "bare-except", "Bare except", "warning", "f.py", 10, "except:"
                ),
                SemanticFinding(
                    "hardcoded-secret", "Secret", "error", "g.py", 5, 'key="abc"'
                ),
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
        from trw_mcp.state.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        mock_result = SemanticCheckResult(
            findings=[
                SemanticFinding(
                    "bare-except", "Bare except", "warning", "f.py", 10, "except:"
                ),
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
        from trw_mcp.state.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = False
        failures: list[ValidationFailure] = []

        _best_effort_semantic_check(config, failures)
        assert len(failures) == 0

    def test_skips_info_severity(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        mock_result = SemanticCheckResult(
            findings=[
                SemanticFinding(
                    "todo-fixme", "TODO comment", "info", "f.py", 1, "TODO"
                ),
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
        from trw_mcp.state.phase_gates_build import _best_effort_semantic_check

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
        from trw_mcp.state.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

        # Create 15 warning findings
        mock_result = SemanticCheckResult(
            findings=[
                SemanticFinding(
                    f"check-{i}", "desc", "warning", "f.py", i, "match"
                )
                for i in range(15)
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

        assert len(failures) == 10  # Capped at 10
