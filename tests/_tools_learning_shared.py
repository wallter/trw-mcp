"""Shared fixtures and helpers for split learning tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests.

    Dedup is disabled to prevent BM25/vector similarity from merging
    entries that tests expect to remain distinct (e.g. "Learning 1" and
    "Learning 2" score >0.85 similarity and would otherwise be merged).
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    return get_tools_sync(make_test_server("learning"))


def _entries_dir(root: Path) -> Path:
    """Build entries directory path from config — no hardcoded strings."""
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


def _write_analytics(root: Path, *, sessions_tracked: int, total_learnings: int) -> None:
    """Write a minimal analytics.yaml for render tests."""
    analytics_path = root / _CFG.trw_dir / _CFG.context_dir / "analytics.yaml"
    analytics_path.parent.mkdir(parents=True, exist_ok=True)
    analytics_path.write_text(
        f"sessions_tracked: {sessions_tracked}\ntotal_learnings: {total_learnings}\n",
        encoding="utf-8",
    )
