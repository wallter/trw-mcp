from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_dry_check,
    _best_effort_semantic_check,
)


class TestCheckNullableDefaults:
    """Tests for _check_nullable_defaults via subprocess mock."""

    def test_detects_nullable_false_column(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.state.validation import phase_gates_build as pgb

        diff_output = (
            "diff --git a/user.py b/user.py\n"
            "+++ b/user.py\n"
            "+    email = Column(String, nullable=False)\n"
            "+    name = Column(String, nullable=False, server_default='anon')\n"
        )

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = diff_output
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pgb._check_nullable_defaults(tmp_path, ["backend/models/database/user.py"])
        assert len(result) == 1
        assert "NOT NULL column" in result[0]
        assert "email" in result[0]

    def test_no_nullable_columns_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.state.validation import phase_gates_build as pgb

        diff_output = "diff --git a/user.py b/user.py\n+++ b/user.py\n+    name = Column(String)\n"

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = diff_output
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pgb._check_nullable_defaults(tmp_path, ["backend/models/database/user.py"])
        assert result == []

    def test_subprocess_error_continues_gracefully(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.SubprocessError("fail")),
        )
        result = pgb._check_nullable_defaults(tmp_path, ["backend/models/database/user.py"])
        assert result == []

    def test_empty_file_list_returns_empty(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        result = pgb._check_nullable_defaults(tmp_path, [])
        assert result == []


class TestBestEffortDryCheckEnabled:
    """Tests for _best_effort_dry_check inner logic when enabled."""

    def test_no_changed_py_files_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["only_a_yaml.yaml"])

        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        assert failures == []

    def test_duplicated_blocks_add_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state import dry_check as dc
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        mock_loc = MagicMock()
        mock_loc.file_path = "src/foo.py"
        mock_loc.start_line = 10
        mock_block = MagicMock()
        mock_block.locations = [mock_loc, mock_loc]
        mock_block.block_hash = "abc123"

        monkeypatch.setattr(dc, "find_duplicated_blocks", lambda *args, **kwargs: [mock_block])

        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        rules = [f.rule for f in failures]
        assert "duplication_detected" in rules

    def test_test_files_excluded_from_dry_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state import dry_check as dc
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["trw-mcp/tests/test_foo.py"])

        called_with: list[list[str]] = []

        def fake_find(files: list[str], **kwargs: object) -> list[object]:
            called_with.extend(files)
            return []

        monkeypatch.setattr(dc, "find_duplicated_blocks", fake_find)
        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        assert not any("/tests/" in f for f in called_with)


class TestBestEffortSemanticCheckEnabled:
    """Tests for _best_effort_semantic_check inner logic when enabled."""

    def test_no_scannable_files_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["only_a_yaml.yaml"])

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []

    def test_semantic_findings_add_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state import semantic_checks as sc
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        mock_finding = MagicMock()
        mock_finding.check_id = "NO_BARE_EXCEPT"
        mock_finding.severity = "warning"
        mock_finding.description = "Bare except clause"
        mock_finding.file_path = "src/foo.py"
        mock_finding.line_number = 42

        mock_result = MagicMock()
        mock_result.findings = [mock_finding]
        monkeypatch.setattr(sc, "run_semantic_checks", lambda _: mock_result)

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        rules = [f.rule for f in failures]
        assert "NO_BARE_EXCEPT" in rules

    def test_info_severity_findings_excluded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state import semantic_checks as sc
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        mock_finding = MagicMock()
        mock_finding.check_id = "STYLE_NOTE"
        mock_finding.severity = "info"

        mock_result = MagicMock()
        mock_result.findings = [mock_finding]
        monkeypatch.setattr(sc, "run_semantic_checks", lambda _: mock_result)

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []
