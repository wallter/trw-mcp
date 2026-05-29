"""Shared fixtures for split learning helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path
