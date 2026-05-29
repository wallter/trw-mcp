"""Tests for semantic review automation integration behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.semantic_checks import SemanticCheckResult, SemanticFinding


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

        assert len(failures) == 0

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
                    _best_effort_semantic_check(config, failures)

        assert len(failures) == 0

    def test_caps_at_10_findings(self) -> None:
        from trw_mcp.models.requirements import ValidationFailure
        from trw_mcp.state.validation.phase_gates_build import _best_effort_semantic_check

        config = MagicMock()
        config.semantic_checks_enabled = True
        failures: list[ValidationFailure] = []

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

        assert len(failures) == 10
