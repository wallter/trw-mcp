"""Tests for shared path resolution in trw_mcp.state._paths."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import resolve_run_path


class TestResolveProjectRoot:
    """Tests for resolve_project_root()."""

    def test_uses_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns TRW_PROJECT_ROOT env var when set."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == tmp_path.resolve()

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns CWD when env var is not set."""
        monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == Path.cwd().resolve()

    def test_resolves_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result.is_absolute()


class TestResolveTrwDir:
    """Tests for resolve_trw_dir()."""

    def test_returns_trw_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns project_root / .trw."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result == tmp_path.resolve() / ".trw"

    def test_is_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result.is_absolute()


class TestResolveRunPath:
    """Tests for resolve_run_path() — PRD-FIX-007."""

    def test_explicit_path_returns_given(self, tmp_path: Path) -> None:
        """FR02a: Explicit run_path resolves when it exists."""
        run = tmp_path / "myrun"
        run.mkdir()
        assert resolve_run_path(str(run)) == run.resolve()

    def test_explicit_nonexistent_raises(self, tmp_path: Path) -> None:
        """FR02a: Non-existent explicit path raises StateError."""
        with pytest.raises(StateError, match="does not exist"):
            resolve_run_path(str(tmp_path / "nonexistent"))

    def test_auto_detect_single_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Auto-detection with single run directory."""
        project = tmp_path / "project"
        run1 = project / "docs" / "task1" / "runs" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        assert resolve_run_path() == run1

    def test_auto_detect_most_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Auto-detection selects most recently modified run.yaml."""
        project = tmp_path / "project"
        run1 = project / "docs" / "task1" / "runs" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        time.sleep(0.05)
        run2 = project / "docs" / "task1" / "runs" / "run-002"
        (run2 / "meta").mkdir(parents=True)
        (run2 / "meta" / "run.yaml").write_text("run_id: run-002\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        assert resolve_run_path() == run2

    def test_no_docs_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02c: StateError when docs/ directory not found."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        with pytest.raises(StateError, match="docs/ directory not found"):
            resolve_run_path()

    def test_empty_runs_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02c: StateError when runs/ exists but contains no run dirs."""
        project = tmp_path / "project"
        (project / "docs" / "task1" / "runs").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        with pytest.raises(StateError, match="No active runs"):
            resolve_run_path()

    def test_ignores_dirs_without_run_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Directories without meta/run.yaml are skipped."""
        project = tmp_path / "project"
        # run with meta/ but no run.yaml — should be skipped
        no_yaml = project / "docs" / "task1" / "runs" / "run-bad"
        (no_yaml / "meta").mkdir(parents=True)
        # run with run.yaml — should be found
        good = project / "docs" / "task1" / "runs" / "run-good"
        (good / "meta").mkdir(parents=True)
        (good / "meta" / "run.yaml").write_text("run_id: run-good\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        assert resolve_run_path() == good

    def test_error_includes_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR06: Error context includes project_root for debugging."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        with pytest.raises(StateError) as exc_info:
            resolve_run_path()
        assert "project_root" in exc_info.value.context
        assert str(project) in str(exc_info.value.context["project_root"])

    def test_auto_detect_across_task_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-detection works across multiple task directories."""
        project = tmp_path / "project"
        run1 = project / "docs" / "task-a" / "runs" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        time.sleep(0.05)
        run2 = project / "docs" / "task-b" / "runs" / "run-002"
        (run2 / "meta").mkdir(parents=True)
        (run2 / "meta" / "run.yaml").write_text("run_id: run-002\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        assert resolve_run_path() == run2

    def test_explicit_path_returns_absolute(self, tmp_path: Path) -> None:
        """Explicit path is resolved to absolute."""
        run = tmp_path / "myrun"
        run.mkdir()
        result = resolve_run_path(str(run))
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# Config-driven task_root wiring — PRD-INFRA-011-FR04
# ---------------------------------------------------------------------------


class TestResolveRunPathConfigWiring:
    """Tests for config-driven task_root in path resolution — PRD-INFRA-011."""

    def test_custom_task_root_auto_detect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR04: task_root='work' → finds runs under work/ instead of docs/."""
        config = TRWConfig(task_root="work")
        monkeypatch.setattr("trw_mcp.state._paths._config", config)

        project = tmp_path / "project"
        run = project / "work" / "task1" / "runs" / "run-001"
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text("run_id: run-001\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        assert resolve_run_path() == run

    def test_custom_task_root_error_no_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR04: task_root='work' → error references 'work/' not 'docs/'."""
        config = TRWConfig(task_root="work")
        monkeypatch.setattr("trw_mcp.state._paths._config", config)

        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        with pytest.raises(StateError, match="work/ directory not found"):
            resolve_run_path()

    def test_custom_task_root_no_runs_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR04: task_root='work' → error references 'work/*/runs/'."""
        config = TRWConfig(task_root="work")
        monkeypatch.setattr("trw_mcp.state._paths._config", config)

        project = tmp_path / "project"
        (project / "work" / "task1" / "runs").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        with pytest.raises(StateError, match=r"work/\*/runs/"):
            resolve_run_path()
