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

    Dedup + embeddings are disabled to keep entries that tests expect to
    remain distinct from being merged/collapsed:

    - ``TRW_DEDUP_ENABLED=false`` stops the store-time semantic dedup from
      merging near-identical fixtures (e.g. "Learning 1" and "Learning 2"
      score >0.85 similarity and would otherwise be merged into one entry).
    - ``TRW_EMBEDDINGS_ENABLED=false`` stops the recall-time near-duplicate
      cosine collapse (F-DEDUP-001 in ``_recall_dedup.py``), which is gated
      on stored embeddings rather than ``dedup_enabled`` and would otherwise
      collapse index-suffixed fixtures ("Cap test entry number 0/1/...")
      down to a single result. Both paths activated for these files once
      commit f4ca661c9 flipped the ``embeddings_enabled`` default to True;
      these are mechanics tests (ranking/paging/counting) orthogonal to
      embedding behavior, which is covered by the dedicated dedup suites.
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
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
