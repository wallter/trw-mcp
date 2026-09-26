"""Shared fixtures and helpers for split learning tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._ide_detection_isolation import isolate_ide_detection
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


@pytest.fixture(autouse=True)
def no_machine_wide_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep client detection inside the temp project, off the developer's PATH.

    ``instructions_sync_fn(client="auto")`` resolves its write targets through
    ``detect_ide``, which reads machine-global signals by design. On a
    workstation with Cursor installed, every empty ``tmp_path`` therefore looks
    like a Cursor project whose profile does not claim CLAUDE.md, so the sync
    correctly wrote nothing and these tests failed on the machine rather than
    on the code. An empty detection list is the "no client identified yet"
    scaffold case these tests mean; a test that wants a specific client passes
    ``client=`` explicitly. Shared helper: ``tests/_ide_detection_isolation``.
    """
    isolate_ide_detection(monkeypatch)


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    return get_tools_sync(make_test_server("learning"))


def instructions_sync_fn(
    scope: str = "root",
    target_dir: str | None = None,
    client: str = "auto",
    dry_run: bool = False,
    force: bool = False,
    *,
    _config: Any | None = None,
) -> dict[str, Any]:
    """Call ``execute_claude_md_sync`` with the removed instructions-sync
    tool's own default parameters (PRD-CORE-300 S6b folded the tool into
    ``trw-mcp instructions sync``; this keeps every pre-existing call site's
    keyword-argument shape intact). ``_config`` overrides the resolved config
    for call sites that patch specific module attributes rather than env vars."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync, instruction_write_trigger
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._learning_module_helpers import _create_llm_client

    with instruction_write_trigger("tool_call", "instructions sync"):
        return dict(
            execute_claude_md_sync(
                scope,
                target_dir,
                _config if _config is not None else get_config(),
                FileStateReader(),
                _create_llm_client(),
                client,
                dry_run=dry_run,
                force=force,
            )
        )


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
