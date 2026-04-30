"""Shared support for split telemetry tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import trw_mcp.tools.telemetry as telemetry
from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.state.persistence import FileStateReader


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read a JSONL file and return list of parsed records."""
    reader = FileStateReader()
    return reader.read_jsonl(path)


def _make_ceremony_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Create a FastMCP server with ceremony tools registered and project root patched."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return get_tools_sync(make_test_server("ceremony"))


def _config_with(**overrides: object) -> Any:
    """Return a copy of the live TRWConfig with attribute overrides applied."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    for attr, val in overrides.items():
        object.__setattr__(cfg, attr, val)
    return cfg


@pytest.fixture(autouse=True)
def reset_telemetry_cache() -> None:
    """Reset the module-level run-dir cache before each test to avoid inter-test pollution."""
    telemetry._cached_run_dir = (0.0, None)


@pytest.fixture()
def trw_root(tmp_path: Path) -> Path:
    """Minimal .trw directory structure for telemetry tests."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with meta/events.jsonl ready."""
    d = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-abcd1234"
    (d / "meta").mkdir(parents=True)
    (d / "meta" / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (d / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return d
