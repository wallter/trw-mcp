"""Tests for semantic review automation check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.semantic_checks import (
    SemanticCheck,
    run_semantic_checks,
)


class TestRunSemanticChecks:
    """FR-2: Automated pattern checks."""

    def test_detects_bare_except(self, tmp_path: Path) -> None:
        file_path = tmp_path / "code.py"
        file_path.write_text("try:\n    x = 1\nexcept:\n    pass\n")

        result = run_semantic_checks([str(file_path)])
        assert result.files_scanned == 1
        bare_excepts = [r for r in result.findings if r.check_id == "bare-except"]
        assert len(bare_excepts) >= 1

    def test_detects_hasattr(self, tmp_path: Path) -> None:
        file_path = tmp_path / "models.py"
        file_path.write_text('if hasattr(user, "email"):\n    pass\n')

        result = run_semantic_checks([str(file_path)])
        hasattr_findings = [r for r in result.findings if r.check_id == "dead-hasattr"]
        assert len(hasattr_findings) >= 1

    def test_detects_print_statements(self, tmp_path: Path) -> None:
        file_path = tmp_path / "debug.py"
        file_path.write_text('print("debug output")\n')

        result = run_semantic_checks([str(file_path)])
        prints = [r for r in result.findings if r.check_id == "print-debug"]
        assert len(prints) >= 1

    def test_detects_todo_comments(self, tmp_path: Path) -> None:
        file_path = tmp_path / "wip.py"
        file_path.write_text("# TODO: fix this\nx = 1\n")

        result = run_semantic_checks([str(file_path)])
        todos = [r for r in result.findings if r.check_id == "todo-fixme"]
        assert len(todos) >= 1

    def test_no_findings_in_clean_code(self, tmp_path: Path) -> None:
        file_path = tmp_path / "clean.py"
        file_path.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

        result = run_semantic_checks([str(file_path)])
        assert len(result.findings) == 0

    def test_skips_nonexistent_files(self, tmp_path: Path) -> None:
        result = run_semantic_checks([str(tmp_path / "nope.py")])
        assert result.files_scanned == 0

    def test_respects_language_filter(self, tmp_path: Path) -> None:
        """Python-specific checks should not run on .ts files."""
        file_path = tmp_path / "code.ts"
        file_path.write_text('print("hello")\n')

        result = run_semantic_checks([str(file_path)])
        prints = [r for r in result.findings if r.check_id == "print-debug"]
        assert len(prints) == 0

    def test_custom_checks(self, tmp_path: Path) -> None:
        file_path = tmp_path / "code.py"
        file_path.write_text("x = MAGIC_NUMBER\n")

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
        result = run_semantic_checks([str(file_path)], checks=custom)
        assert len(result.findings) == 1
        assert result.findings[0].check_id == "magic-number"

    def test_records_matched_text(self, tmp_path: Path) -> None:
        file_path = tmp_path / "code.py"
        file_path.write_text("try:\n    pass\nexcept:\n    pass\n")

        result = run_semantic_checks([str(file_path)])
        bare = [r for r in result.findings if r.check_id == "bare-except"]
        assert len(bare) >= 1
        assert bare[0].matched_text == "except:"
        assert bare[0].line_number == 3

    def test_handles_unreadable_file(self, tmp_path: Path) -> None:
        """Files that raise OSError on read should be skipped."""
        file_path = tmp_path / "unreadable.py"
        file_path.write_text("content")

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = run_semantic_checks([str(file_path)])
        assert result.files_scanned == 0

    def test_skips_non_automated_checks(self, tmp_path: Path) -> None:
        file_path = tmp_path / "code.py"
        file_path.write_text("status = 'admin'\n")

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
        result = run_semantic_checks([str(file_path)], checks=manual_only)
        assert result.checks_run == 0
        assert len(result.findings) == 0

    def test_handles_invalid_regex(self, tmp_path: Path) -> None:
        file_path = tmp_path / "code.py"
        file_path.write_text("some text\n")

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
        result = run_semantic_checks([str(file_path)], checks=bad_regex)
        assert len(result.findings) == 0


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
        first_file = tmp_path / "a.py"
        first_file.write_text("try:\n    pass\nexcept:\n    pass\n")
        second_file = tmp_path / "b.py"
        second_file.write_text("try:\n    pass\nexcept:\n    pass\n")

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
        result = run_semantic_checks([str(first_file), str(second_file)], checks=checks)
        assert result.files_scanned == 2
        assert len(result.findings) == 2

    def test_language_any_matches_all_file_types(self, tmp_path: Path) -> None:
        """A check with language='any' runs against all file types."""
        python_file = tmp_path / "code.py"
        python_file.write_text("# FIXME: broken\n")
        typescript_file = tmp_path / "code.ts"
        typescript_file.write_text("// FIXME: broken\n")
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
        result = run_semantic_checks(
            [str(python_file), str(typescript_file), str(go_file)],
            checks=checks,
        )
        assert result.files_scanned == 3
        assert len(result.findings) == 3

    def test_automated_check_without_pattern_skipped(self, tmp_path: Path) -> None:
        """Automated=True but pattern=None should not produce findings."""
        file_path = tmp_path / "code.py"
        file_path.write_text("anything\n")

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
        result = run_semantic_checks([str(file_path)], checks=checks)
        assert result.checks_run == 0
        assert len(result.findings) == 0

    def test_language_mismatch_skips_check(self, tmp_path: Path) -> None:
        """A go-specific check does not run on python files."""
        file_path = tmp_path / "code.py"
        file_path.write_text("goroutine something\n")

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
        result = run_semantic_checks([str(file_path)], checks=checks)
        assert result.files_scanned == 1
        assert len(result.findings) == 0

    def test_multiple_matches_same_file(self, tmp_path: Path) -> None:
        """Multiple matches on different lines in the same file."""
        file_path = tmp_path / "code.py"
        file_path.write_text("# TODO: first\nx = 1\n# TODO: second\n")

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
        result = run_semantic_checks([str(file_path)], checks=checks)
        assert result.files_scanned == 1
        assert len(result.findings) == 2
        assert result.findings[0].line_number == 1
        assert result.findings[1].line_number == 3

    def test_empty_file_no_findings(self, tmp_path: Path) -> None:
        """An empty file produces no findings."""
        file_path = tmp_path / "empty.py"
        file_path.write_text("")

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
        result = run_semantic_checks([str(file_path)], checks=checks)
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
        file_path = tmp_path / "code.py"
        file_path.write_text("x = CUSTOM_MARKER\n")

        result = run_semantic_checks([str(file_path)], rubric_path=rubric)
        assert len(result.findings) == 1
        assert result.findings[0].check_id == "custom-id"

    def test_finding_attributes(self, tmp_path: Path) -> None:
        """Verify all SemanticFinding attributes are populated correctly."""
        file_path = tmp_path / "target.py"
        file_path.write_text("line_one\nMATCH_HERE\nline_three\n")

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
        result = run_semantic_checks([str(file_path)], checks=checks)
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.check_id == "attr-check"
        assert finding.description == "Attribute test"
        assert finding.severity == "error"
        assert finding.file_path == str(file_path)
        assert finding.line_number == 2
        assert finding.matched_text == "MATCH_HERE"
