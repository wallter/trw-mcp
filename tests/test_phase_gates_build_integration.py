from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_integration_check,
    _best_effort_orphan_check,
)


class TestBestEffortIntegrationCheck:
    """Tests for _best_effort_integration_check inner logic."""

    def test_no_src_dir_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="warning")
        assert failures == []

    def test_unregistered_tools_add_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(ic, "check_integration", lambda _: {"unregistered": ["new_tool"], "missing_tests": []})

        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="error")
        rules = [f.rule for f in failures]
        assert "tool_registration" in rules

    def test_missing_tests_add_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(ic, "check_integration", lambda _: {"unregistered": [], "missing_tests": ["test_foo.py"]})

        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="warning")
        rules = [f.rule for f in failures]
        assert "test_coverage" in rules

    def test_exception_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root")))
        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures)


class TestBestEffortOrphanCheck:
    """Tests for _best_effort_orphan_check inner logic."""

    def test_no_src_dir_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        assert failures == []

    def test_orphan_modules_add_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(ic, "check_orphan_modules", lambda _: {"orphans": ["some_orphan_module"]})

        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        rules = [f.rule for f in failures]
        assert "module_reachability" in rules

    def test_no_orphans_no_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(ic, "check_orphan_modules", lambda _: {"orphans": []})

        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        assert failures == []

    def test_exception_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root")))
        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures)
