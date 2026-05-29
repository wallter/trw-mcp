"""PRD-FIX-083 FR03: trw_session_start MUST NOT trigger the legacy mtime scan.

Mechanical regression guard for the class of perf bug fixed in commits
c7ff20f84, ba328b177 and PRD-FIX-083. If a future caller in the
session_start hot path forgets to pass ``context=`` (or uses
``find_active_run()`` instead of ``get_pinned_run()``), this test fails
loudly instead of silently regressing latency by ~25 s per call.

The test stubs ``FileStateReader.read_yaml`` to count its invocations,
runs ``trw_session_start`` against a fixture project with multiple
``run.yaml`` files, and asserts that no run.yaml outside the (absent)
pinned run was parsed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_writer = FileStateWriter()


def _make_run_dir(trw_dir: Path, task: str, run_id: str, status: str = "active") -> Path:
    """Create a run.yaml under .trw/runs/{task}/{run_id}/meta/."""
    run_dir = trw_dir / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(
        meta / "run.yaml",
        {"run_id": run_id, "task": task, "status": status, "phase": "implement"},
    )
    return run_dir


@pytest.fixture
def fixture_with_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a temp .trw with 5 active run.yaml files but NO pinned run."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    for i in range(5):
        _make_run_dir(trw_dir, task=f"task-{i}", run_id=f"20260503T00000{i}Z-aaaaaaaa")
    # Ensure no pin file exists for this test.
    runtime_dir = trw_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pin_file = runtime_dir / "pins.json"
    if pin_file.exists():
        pin_file.unlink()
    # Point TRW at this temp project.
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return trw_dir


def test_session_start_does_not_scan_run_yamls_when_no_pin(
    fixture_with_runs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh session_start with no pin must NOT read any run.yaml.

    The pin-only contract is: with no pin, return None without scanning.
    A read of ANY run.yaml during session_start is a regression.
    """
    read_yaml_calls: list[Path] = []
    real_read_yaml = FileStateReader.read_yaml

    def counting_read_yaml(self: FileStateReader, path: Path) -> Any:
        read_yaml_calls.append(Path(path))
        return real_read_yaml(self, path)

    monkeypatch.setattr(FileStateReader, "read_yaml", counting_read_yaml)

    # Import lazily so monkeypatch is in effect.
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools._ceremony_helpers import perform_session_recalls

    config = get_config()
    reader = FileStateReader()
    trw_dir = resolve_trw_dir()

    # Run the recall step plus the run-resolution path that session_start hits.
    # If any ctx-less caller falls through to the legacy scan, it will read
    # one or more run.yaml files and the assertion below will fail.
    learnings, _auto, _extra = perform_session_recalls(trw_dir, query="*", config=config, reader=reader)

    # Assertion: no run.yaml was parsed during the recall + resolution path.
    yaml_calls = [p for p in read_yaml_calls if p.name == "run.yaml"]
    assert yaml_calls == [], (
        f"trw_session_start hot path read {len(yaml_calls)} run.yaml file(s) "
        f"despite having no pinned run. This indicates a ctx-aware suppression "
        f"leak (PRD-FIX-083). Files read: {yaml_calls}"
    )


def test_session_start_reads_only_pinned_run_yaml_when_pin_exists(
    fixture_with_runs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a pin exists, only the pinned run.yaml may be read (status only).

    This is the inverse of the prior test — confirms the pin-only path
    DOES read its own run.yaml when needed (e.g., to populate phase/task
    on the result), but does not scan any other run.yaml.
    """
    trw_dir = fixture_with_runs

    # Pin one of the runs.
    pinned_task = "task-2"
    pinned_run_id = "20260503T000002Z-aaaaaaaa"
    pinned_run = trw_dir / "runs" / pinned_task / pinned_run_id

    from trw_mcp.state._paths import _pinned_runs, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache

    _pinned_runs.clear()
    invalidate_pin_store_cache()
    pin_active_run(pinned_run)

    read_yaml_calls: list[Path] = []
    real_read_yaml = FileStateReader.read_yaml

    def counting_read_yaml(self: FileStateReader, path: Path) -> Any:
        read_yaml_calls.append(Path(path))
        return real_read_yaml(self, path)

    monkeypatch.setattr(FileStateReader, "read_yaml", counting_read_yaml)

    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools._ceremony_helpers import perform_session_recalls

    config = get_config()
    reader = FileStateReader()
    trw_dir2 = resolve_trw_dir()

    perform_session_recalls(trw_dir2, query="*", config=config, reader=reader)

    # Only run.yaml reads should be against the pinned run, if any.
    yaml_calls = [p for p in read_yaml_calls if p.name == "run.yaml"]
    non_pinned = [p for p in yaml_calls if pinned_run_id not in str(p)]
    assert non_pinned == [], (
        f"trw_session_start hot path read non-pinned run.yaml file(s): {non_pinned}. "
        f"Only the pinned run ({pinned_run_id}) may be read."
    )
