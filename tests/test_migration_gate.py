"""Tests for migration verification gate (PRD-INFRA-035)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.requirements import ValidationFailure


class TestGetChangedFiles:
    """FR-1: Detect model file changes via git."""

    def test_returns_changed_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="backend/models/database.py\n"),
                MagicMock(stdout=""),
                MagicMock(stdout=""),
            ]
            result = _get_changed_files(tmp_path)
            assert "backend/models/database.py" in result

    def test_returns_empty_on_git_failure(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            result = _get_changed_files(tmp_path)
            assert result == []

    def test_deduplicates_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="file.py\n"),
                MagicMock(stdout="file.py\n"),  # same file staged
                MagicMock(stdout=""),
            ]
            result = _get_changed_files(tmp_path)
            assert result.count("file.py") == 1

    def test_includes_untracked_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=""),
                MagicMock(stdout=""),
                MagicMock(stdout="backend/models/database.py\n"),
            ]
            result = _get_changed_files(tmp_path)
            assert "backend/models/database.py" in result

    def test_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("disk error")
            result = _get_changed_files(tmp_path)
            assert result == []

    def test_returns_empty_on_subprocess_error(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("git timeout")
            result = _get_changed_files(tmp_path)
            assert result == []

    def test_merges_all_three_sources(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _get_changed_files

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="a.py\n"),      # diff HEAD
                MagicMock(stdout="b.py\n"),      # cached/staged
                MagicMock(stdout="c.py\n"),      # untracked
            ]
            result = _get_changed_files(tmp_path)
            assert sorted(result) == ["a.py", "b.py", "c.py"]


class TestCheckNullableDefaults:
    """FR-3: NOT NULL + server_default check."""

    def test_warns_nullable_false_without_server_default(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff_output = (
            "+    status = Column(String(32), nullable=False, default='active')\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=diff_output)
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 1
            assert "NOT NULL column without server_default" in warnings[0]

    def test_no_warning_with_server_default(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff_output = (
            "+    status = Column(String(32), nullable=False, server_default='active')\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=diff_output)
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 0

    def test_no_warning_for_nullable_true(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff_output = (
            "+    name = Column(String(100))\n"  # nullable defaults to True
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=diff_output)
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 0

    def test_ignores_removed_lines(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff_output = (
            "-    status = Column(String(32), nullable=False)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=diff_output)
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 0

    def test_ignores_diff_header_lines(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff_output = (
            "+++ b/backend/models/database.py\n"
            "+    status = Column(String(32), nullable=False)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=diff_output)
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 1
            assert "NOT NULL column without server_default" in warnings[0]

    def test_handles_subprocess_error(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("git failed")
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 0

    def test_handles_multiple_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        diff1 = "+    col1 = Column(Integer, nullable=False)\n"
        diff2 = "+    col2 = Column(String, nullable=False)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=diff1),
                MagicMock(stdout=diff2),
            ]
            warnings = _check_nullable_defaults(
                tmp_path, ["models/a.py", "models/b.py"]
            )
            assert len(warnings) == 2

    def test_handles_file_not_found(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import _check_nullable_defaults

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            warnings = _check_nullable_defaults(tmp_path, ["backend/models/database.py"])
            assert len(warnings) == 0


class TestCheckMigrationGate:
    """FR-2: Model changes without migration detection."""

    def test_warns_model_change_without_migration(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = ["backend/models/database.py"]
            with patch(
                "trw_mcp.state.phase_gates_build._check_nullable_defaults"
            ) as mock_null:
                mock_null.return_value = []
                warnings = check_migration_gate(tmp_path)
                assert len(warnings) == 1
                assert "no new Alembic migration detected" in warnings[0]

    def test_no_warning_with_migration(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = [
                "backend/models/database.py",
                "backend/alembic/versions/001_add_status.py",
            ]
            with patch(
                "trw_mcp.state.phase_gates_build._check_nullable_defaults"
            ) as mock_null:
                mock_null.return_value = []
                warnings = check_migration_gate(tmp_path)
                assert len(warnings) == 0

    def test_no_warning_when_no_model_changes(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = ["backend/routers/admin.py"]
            warnings = check_migration_gate(tmp_path)
            assert len(warnings) == 0

    def test_no_warning_when_no_changes(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = []
            warnings = check_migration_gate(tmp_path)
            assert len(warnings) == 0

    def test_combines_migration_and_nullable_warnings(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = ["backend/models/database.py"]
            with patch(
                "trw_mcp.state.phase_gates_build._check_nullable_defaults"
            ) as mock_null:
                mock_null.return_value = [
                    "NOT NULL column without server_default: ..."
                ]
                warnings = check_migration_gate(tmp_path)
                assert len(warnings) == 2  # migration warning + nullable warning

    def test_detects_nested_model_path(self, tmp_path: Path) -> None:
        from trw_mcp.state.phase_gates_build import check_migration_gate

        with patch("trw_mcp.state.phase_gates_build._get_changed_files") as mock_files:
            mock_files.return_value = [
                "some/project/models/database_v2.py",
            ]
            with patch(
                "trw_mcp.state.phase_gates_build._check_nullable_defaults"
            ) as mock_null:
                mock_null.return_value = []
                warnings = check_migration_gate(tmp_path)
                assert len(warnings) == 1
                assert "no new Alembic migration detected" in warnings[0]


class TestBestEffortMigrationCheck:
    """FR-4: Integration with trw_build_check."""

    def test_appends_warnings_as_validation_failures(self) -> None:
        from trw_mcp.state.phase_gates_build import _best_effort_migration_check

        config = MagicMock()
        config.migration_gate_enabled = True
        failures: list[ValidationFailure] = []

        with patch(
            "trw_mcp.state.phase_gates_build.check_migration_gate"
        ) as mock_gate:
            mock_gate.return_value = [
                "database.py modified but no new Alembic migration detected"
            ]
            with patch(
                "trw_mcp.state._paths.resolve_project_root"
            ) as mock_root:
                mock_root.return_value = Path("/project")
                _best_effort_migration_check(config, failures)

                assert len(failures) == 1
                assert failures[0].severity == "warning"
                assert "migration" in failures[0].message.lower()

    def test_skipped_when_disabled(self) -> None:
        from trw_mcp.state.phase_gates_build import _best_effort_migration_check

        config = MagicMock()
        config.migration_gate_enabled = False
        failures: list[ValidationFailure] = []

        _best_effort_migration_check(config, failures)
        assert len(failures) == 0

    def test_never_raises(self) -> None:
        from trw_mcp.state.phase_gates_build import _best_effort_migration_check

        config = MagicMock()
        config.migration_gate_enabled = True
        failures: list[ValidationFailure] = []

        with patch(
            "trw_mcp.state.phase_gates_build.check_migration_gate"
        ) as mock_gate:
            mock_gate.side_effect = RuntimeError("unexpected")
            with patch(
                "trw_mcp.state._paths.resolve_project_root"
            ) as mock_root:
                mock_root.return_value = Path("/project")
                # Should not raise
                _best_effort_migration_check(config, failures)
                assert len(failures) == 0

    def test_multiple_warnings_become_multiple_failures(self) -> None:
        from trw_mcp.state.phase_gates_build import _best_effort_migration_check

        config = MagicMock()
        config.migration_gate_enabled = True
        failures: list[ValidationFailure] = []

        with patch(
            "trw_mcp.state.phase_gates_build.check_migration_gate"
        ) as mock_gate:
            mock_gate.return_value = [
                "database.py modified but no new Alembic migration detected",
                "NOT NULL column without server_default in database.py: col = Column(Integer, nullable=False)",
            ]
            with patch(
                "trw_mcp.state._paths.resolve_project_root"
            ) as mock_root:
                mock_root.return_value = Path("/project")
                _best_effort_migration_check(config, failures)

                assert len(failures) == 2
                assert all(f.severity == "warning" for f in failures)
                assert all(f.field == "migration_gate" for f in failures)
                assert all(f.rule == "migration_check" for f in failures)

    def test_does_not_modify_existing_failures(self) -> None:
        from trw_mcp.state.phase_gates_build import _best_effort_migration_check

        config = MagicMock()
        config.migration_gate_enabled = True
        existing = ValidationFailure(
            field="other", rule="other_rule", message="pre-existing"
        )
        failures: list[ValidationFailure] = [existing]

        with patch(
            "trw_mcp.state.phase_gates_build.check_migration_gate"
        ) as mock_gate:
            mock_gate.return_value = ["some warning"]
            with patch(
                "trw_mcp.state._paths.resolve_project_root"
            ) as mock_root:
                mock_root.return_value = Path("/project")
                _best_effort_migration_check(config, failures)

                assert len(failures) == 2
                assert failures[0] is existing
