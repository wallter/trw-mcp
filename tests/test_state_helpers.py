"""Tests for trw_mcp.state._helpers shared utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._helpers import (
    is_active_entry,
    iter_yaml_entry_files,
    load_project_config,
    read_jsonl_resilient,
    read_jsonl_tail,
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


class TestReadJsonlTail:
    """Tests for read_jsonl_tail() per-line-resilient JSONL tail reader."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_jsonl_tail(tmp_path / "absent.jsonl", 10) == []

    def test_reads_all_records(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_returns_only_last_max_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text("".join(f'{{"i": {i}}}\n' for i in range(10)))
        result = read_jsonl_tail(path, 3)
        assert result == [{"i": 7}, {"i": 8}, {"i": 9}]

    def test_skips_corrupt_line_keeps_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('{"a": 1}\n{broken\n{"a": 2}\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}, {"a": 2}]

    def test_skips_non_object_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('[1, 2]\n{"a": 1}\n42\n"str"\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}]

    def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('\n{"a": 1}\n\n\n{"a": 2}\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}, {"a": 2}]

    def test_all_corrupt_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text("not json\n{also broken\n")
        assert read_jsonl_tail(path, 10) == []

    def test_non_utf8_line_skipped_valid_tail_survives(self, tmp_path: Path) -> None:
        """A single non-UTF-8 byte row is dropped, not the whole tail.

        Per-line decoding means a torn append carrying raw non-UTF-8 bytes
        only loses its own line; the surrounding valid records survive.
        """
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"a": 1}\n\xff\xfe not utf8\n{"a": 2}\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}, {"a": 2}]

    def test_non_utf8_trailing_line_skipped(self, tmp_path: Path) -> None:
        """A non-UTF-8 byte row at the tail end does not erase earlier records."""
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"a": 1}\n\xff\xfe not utf8\n')
        assert read_jsonl_tail(path, 10) == [{"a": 1}]

    def test_non_utf8_inside_json_string_dropped(self, tmp_path: Path) -> None:
        """An invalid byte inside an otherwise-JSON line drops that line only.

        The undecodable bytes never reach json.loads, so no mangled
        replacement-character record leaks into the result.
        """
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"k": 1}\n{"k": "\xff"}\n{"k": 2}\n')
        assert read_jsonl_tail(path, 10) == [{"k": 1}, {"k": 2}]

    def test_all_lines_non_utf8_returns_empty(self, tmp_path: Path) -> None:
        """When every line is undecodable the reader still fails open to []."""
        path = tmp_path / "log.jsonl"
        path.write_bytes(b"\xff\xfe\n\xfc\xfd\n")
        assert read_jsonl_tail(path, 10) == []

    def test_max_entries_counts_bytes_lines_before_decode(self, tmp_path: Path) -> None:
        """The tail window is taken on raw lines; a dropped bad row is not refilled."""
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"i": 1}\n\xff bad\n{"i": 3}\n')
        # Window of 2 = last two raw lines (bad + {"i": 3}); bad is dropped.
        assert read_jsonl_tail(path, 2) == [{"i": 3}]


class TestReadJsonlResilient:
    """Tests for read_jsonl_resilient() full-scan per-line-resilient reader."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_jsonl_resilient(tmp_path / "absent.jsonl") == []

    def test_reads_all_records_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
        assert read_jsonl_resilient(path) == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_no_tail_window_returns_full_history(self, tmp_path: Path) -> None:
        """Unlike read_jsonl_tail, the full-scan reader keeps every record."""
        path = tmp_path / "log.jsonl"
        path.write_text("".join(f'{{"i": {i}}}\n' for i in range(50)))
        result = read_jsonl_resilient(path)
        assert len(result) == 50
        assert result[0] == {"i": 0}
        assert result[-1] == {"i": 49}

    def test_skips_torn_line_keeps_rest(self, tmp_path: Path) -> None:
        """A single torn concurrent append drops one line, not the history.

        This is the regression the reader exists to prevent: the strict
        FileStateReader.read_jsonl raises StateError here, aborting the whole
        read; the resilient reader returns the surviving valid records.
        """
        path = tmp_path / "log.jsonl"
        path.write_text('{"a": 1}\n{"a": 2}\n{partial-torn-append')
        assert read_jsonl_resilient(path) == [{"a": 1}, {"a": 2}]

    def test_skips_corrupt_middle_line(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('{"a": 1}\n{broken\n{"a": 2}\n')
        assert read_jsonl_resilient(path) == [{"a": 1}, {"a": 2}]

    def test_skips_non_object_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('[1, 2]\n{"a": 1}\n42\n"str"\n')
        assert read_jsonl_resilient(path) == [{"a": 1}]

    def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text('\n{"a": 1}\n\n\n{"a": 2}\n')
        assert read_jsonl_resilient(path) == [{"a": 1}, {"a": 2}]

    def test_all_corrupt_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text("not json\n{also broken\n")
        assert read_jsonl_resilient(path) == []

    def test_non_utf8_line_skipped_valid_survives(self, tmp_path: Path) -> None:
        """A single non-UTF-8 byte row is dropped, not the whole file."""
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"a": 1}\n\xff\xfe not utf8\n{"a": 2}\n')
        assert read_jsonl_resilient(path) == [{"a": 1}, {"a": 2}]

    def test_non_utf8_inside_json_string_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"k": 1}\n{"k": "\xff"}\n{"k": 2}\n')
        assert read_jsonl_resilient(path) == [{"k": 1}, {"k": 2}]

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text("")
        assert read_jsonl_resilient(path) == []


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
