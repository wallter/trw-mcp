"""Shared fixtures for split ceremony helper tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def _default_embedding_downloads_offline(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep the general suite hermetic while online-boundary tests opt in.

    Session-start enables embeddings by default and may otherwise launch a
    daemon Hugging Face warm-up whose sockets and event loop outlive the test.
    The two dedicated modules below own and drain/mock that lifecycle.
    """
    online_owner_modules = {"test_embedder_warmup.py", "test_embeddings_offline.py"}
    if request.path.name not in online_owner_modules:
        monkeypatch.setenv("TRW_OFFLINE", "1")
    yield


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create minimal .trw structure."""
    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "learnings" / "receipts").mkdir(parents=True)
    (trw / "context").mkdir(parents=True)
    (trw / "memory").mkdir(parents=True)
    return trw


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    directory = tmp_path / "docs" / "task" / "runs" / "20260301T120000Z-test"
    meta = directory / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return directory


@pytest.fixture()
def config() -> TRWConfig:
    """Test configuration."""
    return TRWConfig()


@pytest.fixture()
def reader() -> FileStateReader:
    return FileStateReader()


@pytest.fixture()
def writer() -> FileStateWriter:
    return FileStateWriter()


@pytest.fixture()
def event_logger(writer: FileStateWriter) -> FileEventLogger:
    return FileEventLogger(writer)


def write_installed_version(trw_dir: Path, version: str) -> None:
    """Write the installed-version sentinel used by maintenance tests."""
    sentinel = trw_dir / "installed-version.json"
    sentinel.write_text(
        json.dumps({"version": version, "timestamp": "2026-03-14T00:00:00Z"}),
        encoding="utf-8",
    )
