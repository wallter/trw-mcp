"""Tests for delivery gate — complexity drift detection (R-02 + R-05).

Covers _check_complexity_drift(): re-evaluate complexity at delivery time
by comparing actual file_modified event count against the initial
complexity_signals.files_affected estimate from run.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_helpers import (
    _check_complexity_drift,
    _read_run_events,
    _read_run_yaml,
    check_delivery_gates,
)


def _write_run_yaml(
    run_dir: Path,
    *,
    complexity_class: str = "MINIMAL",
    files_affected: int = 1,
) -> None:
    """Write a minimal run.yaml with complexity fields."""
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    content = (
        f"run_id: test-run\n"
        f"status: active\n"
        f"phase: implement\n"
        f"task_name: test-task\n"
        f"complexity_class: {complexity_class}\n"
        f"complexity_signals:\n"
        f"  files_affected: {files_affected}\n"
        f"  novel_patterns: false\n"
        f"  cross_cutting: false\n"
    )
    (meta / "run.yaml").write_text(content, encoding="utf-8")


def _write_file_modified_events(
    run_dir: Path,
    count: int,
) -> None:
    """Write N file_modified events to events.jsonl."""
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i in range(count):
        event = {
            "ts": f"2026-03-01T12:0{i % 10}:00Z",
            "event": "file_modified",
            "data": {"path": f"src/module_{i}.py"},
        }
        lines.append(json.dumps(event))
    # Also add some non-file_modified events to ensure we only count file_modified
    lines.append(json.dumps({"ts": "2026-03-01T12:00:00Z", "event": "checkpoint"}))
    lines.append(json.dumps({"ts": "2026-03-01T12:00:01Z", "event": "build_check_complete"}))
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def reader() -> FileStateReader:
    return FileStateReader()


@pytest.mark.integration
class TestCheckComplexityDrift:
    """Complexity drift detection at delivery time (R-02 + R-05)."""

    def test_complexity_drift_warning_fires(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with files_affected=1 but 13 file_modified events -> warning."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 13)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is not None
        assert "Complexity drift detected" in result
        assert "MINIMAL" in result
        assert "1 files planned" in result
        assert "13 files were modified" in result
        assert "REVIEW" in result

    def test_complexity_drift_no_warning_for_standard(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """STANDARD classification -> no warning even with many files."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="STANDARD", files_affected=3)
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_below_threshold(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with only 4 file_modified events -> no warning (<=5 threshold)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 4)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_when_estimate_accurate(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with files_affected=5 and actual=6 -> no warning (not >2x)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=5)
        _write_file_modified_events(run_dir, 6)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_exactly_at_threshold_no_warning(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Exactly 5 files and exactly 2x -> no warning (requires >5 AND >2x)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=2)
        _write_file_modified_events(run_dir, 5)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        # 5 is not >5, so no warning
        assert result is None

    def test_complexity_drift_failopen_on_missing_run_yaml(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Missing run.yaml -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_failopen_on_missing_events(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Missing events.jsonl -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        # Remove events.jsonl if it was created
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.exists():
            events_path.unlink()

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_failopen_on_corrupt_yaml(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Corrupt run.yaml -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("{{invalid yaml", encoding="utf-8")
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_when_no_complexity_class(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """run.yaml without complexity_class -> returns None."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: implement\n",
            encoding="utf-8",
        )
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_for_comprehensive(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """COMPREHENSIVE classification -> no warning (only fires for MINIMAL)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="COMPREHENSIVE", files_affected=2)
        _write_file_modified_events(run_dir, 30)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None


@pytest.mark.integration
class TestCheckDeliveryGatesComplexityDrift:
    """Wiring test: complexity drift flows through check_delivery_gates."""

    def test_drift_warning_surfaces_in_delivery_gates(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates includes complexity_drift_warning when drift detected."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 13)

        result = check_delivery_gates(run_dir, reader)

        assert "complexity_drift_warning" in result
        assert "Complexity drift detected" in str(result["complexity_drift_warning"])

    def test_no_drift_warning_in_delivery_gates_when_under_threshold(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates omits complexity_drift_warning when no drift."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 3)

        result = check_delivery_gates(run_dir, reader)

        assert "complexity_drift_warning" not in result
