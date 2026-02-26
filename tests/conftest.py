"""Shared test fixtures for TRW MCP test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, FileEventLogger

# --- Marker auto-assignment ---

_UNIT_FILES: frozenset[str] = frozenset({
    "test_models.py",
    "test_scoring.py",
})

_E2E_FILES: frozenset[str] = frozenset()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """Auto-assign unit/integration/e2e markers to tests without explicit markers."""
    for item in items:
        has_tier = any(
            m.name in ("unit", "integration", "e2e")
            for m in item.iter_markers()
        )
        if has_tier:
            continue

        filename = Path(item.fspath).name
        if filename in _UNIT_FILES:
            item.add_marker(pytest.mark.unit)
        elif filename in _E2E_FILES:
            item.add_marker(pytest.mark.e2e)
        else:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _reset_config_singleton() -> Iterator[None]:
    """Reset TRWConfig singleton for test isolation."""
    _reset_config()
    yield
    _reset_config()


@pytest.fixture(autouse=True)
def _reset_memory_backend() -> Iterator[None]:
    """Reset memory adapter singleton for test isolation."""
    from trw_mcp.state.memory_adapter import reset_backend
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with .trw/ structure.

    Returns:
        Path to the temporary project root.
    """
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "scripts").mkdir()
    (trw_dir / "patterns").mkdir()
    (trw_dir / "context").mkdir()
    return tmp_path


@pytest.fixture
def config(tmp_path: Path) -> TRWConfig:
    """Provide test configuration with temp directory overrides."""
    return TRWConfig(trw_dir=str(tmp_path / ".trw"))


@pytest.fixture
def reader() -> FileStateReader:
    """Provide a FileStateReader instance."""
    return FileStateReader()


@pytest.fixture
def writer() -> FileStateWriter:
    """Provide a FileStateWriter instance."""
    return FileStateWriter()


@pytest.fixture
def event_logger(writer: FileStateWriter) -> FileEventLogger:
    """Provide a FileEventLogger instance."""
    return FileEventLogger(writer)


@pytest.fixture
def sample_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a sample run directory with minimal state.

    Returns:
        Path to the run directory.
    """
    run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260206T120000Z-abcd1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "artifacts").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "scratch" / "_blackboard").mkdir(parents=True)
    (run_dir / "shards").mkdir()
    (run_dir / "validation").mkdir()

    # Write run.yaml
    writer.write_yaml(meta / "run.yaml", {
        "run_id": "20260206T120000Z-abcd1234",
        "task": "test-task",
        "framework": "v18.0_TRW",
        "status": "active",
        "phase": "research",
        "confidence": "medium",
    })

    # Write events.jsonl
    writer.append_jsonl(meta / "events.jsonl", {
        "ts": "2026-02-06T12:00:00Z",
        "event": "run_init",
        "task": "test-task",
    })

    return run_dir
