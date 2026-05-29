from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_dry_check,
    _best_effort_migration_check,
    _best_effort_semantic_check,
    check_migration_gate,
)


class TestCheckMigrationGate:
    """Tests for check_migration_gate (PRD-INFRA-035)."""

    def test_no_changed_files_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: [])
        result = check_migration_gate(tmp_path)
        assert result == []

    def test_model_change_without_migration_adds_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["backend/models/database/user.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(pgb, "_check_nullable_defaults", lambda _root, _files: [])

        result = check_migration_gate(tmp_path)
        assert len(result) == 1
        assert "model" in result[0].lower() or "migration" in result[0].lower()

    def test_model_change_with_migration_no_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = [
            "backend/models/database/user.py",
            "backend/alembic/versions/0001_add_user.py",
        ]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(pgb, "_check_nullable_defaults", lambda _root, _files: [])

        result = check_migration_gate(tmp_path)
        assert result == []

    def test_nullable_default_warnings_appended(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["backend/models/database/user.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(
            pgb,
            "_check_nullable_defaults",
            lambda _root, _files: ["NOT NULL column without server_default in user.py: col = Column(...)"],
        )

        result = check_migration_gate(tmp_path)
        assert any("NOT NULL" in w for w in result)

    def test_non_model_files_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["trw-mcp/src/trw_mcp/tools/ceremony.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)

        result = check_migration_gate(tmp_path)
        assert result == []


class TestBestEffortMigrationCheck:
    """Tests for _best_effort_migration_check."""

    def test_disabled_returns_immediately(self) -> None:
        config = TRWConfig(migration_gate_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_migration_check(config, failures)
        assert failures == []

    def test_enabled_appends_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            pgb,
            "check_migration_gate",
            lambda _: ["model changed without migration"],
        )

        config = TRWConfig(migration_gate_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_migration_check(config, failures)
        rules = [f.rule for f in failures]
        assert "migration_check" in rules

    def test_exception_in_check_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root")))
        config = TRWConfig(migration_gate_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_migration_check(config, failures)


class TestBestEffortDryCheck:
    """Tests for _best_effort_dry_check disabled path."""

    def test_disabled_returns_immediately(self) -> None:
        config = TRWConfig(dry_check_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        assert failures == []

    def test_exception_in_check_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root")))
        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)


class TestBestEffortSemanticCheck:
    """Tests for _best_effort_semantic_check disabled path."""

    def test_disabled_returns_immediately(self) -> None:
        config = TRWConfig(semantic_checks_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []

    def test_exception_in_check_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root")))
        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
