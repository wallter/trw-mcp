"""Tests for trw_mcp.state._helpers shared utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._helpers import (
    is_active_entry,
    iter_yaml_entry_files,
    load_project_config,
    safe_float,
    safe_int,
    safe_str,
)
from trw_mcp.state._paths import iter_run_dirs


class TestSafeInt:
    """Tests for safe_int()."""

    def test_int_value(self) -> None:
        assert safe_int({"x": 42}, "x") == 42

    def test_string_value(self) -> None:
        assert safe_int({"x": "99"}, "x") == 99

    def test_float_string_returns_default(self) -> None:
        # "3.7" can't be parsed by int(), returns default
        assert safe_int({"x": 3.7}, "x") == 0

    def test_missing_key(self) -> None:
        assert safe_int({}, "x") == 0

    def test_missing_key_custom_default(self) -> None:
        assert safe_int({}, "x", default=-1) == -1

    def test_none_value_returns_default(self) -> None:
        assert safe_int({"x": None}, "x") == 0

    def test_invalid_string_returns_default(self) -> None:
        assert safe_int({"x": "not-a-number"}, "x") == 0

    def test_empty_string_returns_default(self) -> None:
        assert safe_int({"x": ""}, "x") == 0


class TestSafeFloat:
    """Tests for safe_float()."""

    def test_float_value(self) -> None:
        assert safe_float({"x": 3.14}, "x") == pytest.approx(3.14)

    def test_int_value(self) -> None:
        assert safe_float({"x": 7}, "x") == 7.0

    def test_string_value(self) -> None:
        assert safe_float({"x": "2.5"}, "x") == 2.5

    def test_missing_key(self) -> None:
        assert safe_float({}, "x") == 0.0

    def test_missing_key_custom_default(self) -> None:
        assert safe_float({}, "x", default=1.0) == 1.0

    def test_invalid_string_returns_default(self) -> None:
        assert safe_float({"x": "abc"}, "x") == 0.0


class TestSafeStr:
    """Tests for safe_str()."""

    def test_string_value(self) -> None:
        assert safe_str({"x": "hello"}, "x") == "hello"

    def test_int_value(self) -> None:
        assert safe_str({"x": 42}, "x") == "42"

    def test_none_value(self) -> None:
        assert safe_str({"x": None}, "x") == ""

    def test_missing_key(self) -> None:
        assert safe_str({}, "x") == ""

    def test_missing_key_custom_default(self) -> None:
        assert safe_str({}, "x", default="fallback") == "fallback"


class TestIterYamlEntryFiles:
    """Tests for iter_yaml_entry_files()."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert list(iter_yaml_entry_files(tmp_path)) == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        assert list(iter_yaml_entry_files(tmp_path / "nope")) == []

    def test_skips_index_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "index.yaml").write_text("x: 1")
        (tmp_path / "entry-1.yaml").write_text("id: 1")
        result = list(iter_yaml_entry_files(tmp_path))
        assert len(result) == 1
        assert result[0].name == "entry-1.yaml"

    def test_yields_sorted_paths(self, tmp_path: Path) -> None:
        (tmp_path / "c.yaml").write_text("id: c")
        (tmp_path / "a.yaml").write_text("id: a")
        (tmp_path / "b.yaml").write_text("id: b")
        result = [p.name for p in iter_yaml_entry_files(tmp_path)]
        assert result == ["a.yaml", "b.yaml", "c.yaml"]

    def test_ignores_non_yaml_files(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("text")
        (tmp_path / "entry.yaml").write_text("id: 1")
        result = list(iter_yaml_entry_files(tmp_path))
        assert len(result) == 1


class TestIsActiveEntry:
    """Tests for is_active_entry()."""

    def test_active_status(self) -> None:
        assert is_active_entry({"status": "active"}) is True

    def test_resolved_status(self) -> None:
        assert is_active_entry({"status": "resolved"}) is False

    def test_obsolete_status(self) -> None:
        assert is_active_entry({"status": "obsolete"}) is False

    def test_missing_status_defaults_active(self) -> None:
        assert is_active_entry({}) is True

    def test_none_status(self) -> None:
        assert is_active_entry({"status": None}) is False


class TestLoadProjectConfig:
    """Tests for load_project_config()."""

    def test_missing_config_returns_defaults(self, tmp_path: Path) -> None:
        result = load_project_config(tmp_path)
        from trw_mcp.models.config import TRWConfig

        assert isinstance(result, TRWConfig)

    def test_loads_existing_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("parallelism_max: 10\n")
        result = load_project_config(tmp_path)
        assert result.parallelism_max == 10

    def test_ignores_none_values(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("parallelism_max: null\n")
        result = load_project_config(tmp_path)
        from trw_mcp.models.config import TRWConfig

        assert result.parallelism_max == TRWConfig().parallelism_max


def _make_run(runs_root: Path, task: str, run_name: str) -> Path:
    """Helper: create a minimal run directory with run.yaml."""
    run_dir = runs_root / task / run_name
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(f"run_id: {run_name}\nstatus: active\n")
    return run_dir


class TestIterRunDirs:
    """Tests for iter_run_dirs() shared generator."""

    def test_empty_task_root(self, tmp_path: Path) -> None:
        assert list(iter_run_dirs(tmp_path)) == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        assert list(iter_run_dirs(tmp_path / "nope")) == []

    def test_single_run(self, tmp_path: Path) -> None:
        run_dir = _make_run(tmp_path, "task-a", "run-001")
        results = list(iter_run_dirs(tmp_path))
        assert len(results) == 1
        assert results[0][0] == run_dir
        assert results[0][1] == run_dir / "meta" / "run.yaml"

    def test_multiple_runs_sorted(self, tmp_path: Path) -> None:
        _make_run(tmp_path, "task-a", "run-002")
        _make_run(tmp_path, "task-a", "run-001")
        _make_run(tmp_path, "task-b", "run-003")
        results = list(iter_run_dirs(tmp_path))
        names = [r[0].name for r in results]
        assert names == ["run-001", "run-002", "run-003"]

    def test_skips_dirs_without_run_yaml(self, tmp_path: Path) -> None:
        # Create a run dir missing run.yaml
        (tmp_path / "task-a" / "run-bad" / "meta").mkdir(parents=True)
        _make_run(tmp_path, "task-a", "run-good")
        results = list(iter_run_dirs(tmp_path))
        assert len(results) == 1
        assert results[0][0].name == "run-good"

    def test_skips_task_without_run_subdirs(self, tmp_path: Path) -> None:
        (tmp_path / "task-no-runs").mkdir()  # Empty task dir — no run subdirs
        _make_run(tmp_path, "task-ok", "run-001")
        results = list(iter_run_dirs(tmp_path))
        assert len(results) == 1
