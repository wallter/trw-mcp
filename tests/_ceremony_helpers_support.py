"""Shared fixtures for split ceremony helper tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def _no_platform_contact_by_default(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep the general suite hermetic while online-boundary tests opt in.

    The update check and the team-sync pull are the only automatic platform
    contacts, and both ask ``platform_contact_enabled`` first. The general suite
    has it answer False at both consumer sites; the modules that exercise those
    contacts' own request/header behavior mock the HTTP client directly and
    drive the switch themselves. Model loads need no guard: runtime loads are
    cache-only (PRD-CORE-302 W40).
    """
    online_owner_modules = {
        "test_auto_upgrade_credential_egress.py",
        "test_auto_upgrade_update_checks.py",
        "test_platform_trust.py",
        "test_sync_pull.py",
    }
    if request.path.name not in online_owner_modules:
        disable_platform_contact(monkeypatch)
    yield


def disable_platform_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make both automatic platform contacts see the switch turned off."""
    import trw_mcp.state.auto_upgrade as auto_upgrade
    import trw_mcp.sync.pull as pull

    monkeypatch.setattr(auto_upgrade, "_platform_contact_enabled", lambda: False)
    monkeypatch.setattr(pull, "platform_contact_enabled", lambda: False)


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
