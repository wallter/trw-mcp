"""Shared helpers for core084 ceremony adaptation test splits."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig


def _run_agents_md_sync(
    tmp_path: Path,
    ceremony_mode: str = "full",
) -> dict[str, object]:
    """Run execute_claude_md_sync with mocked infra targeting AGENTS.md.

    Driven through ``cursor-cli`` rather than ``opencode``. PRD-CORE-084 is about
    AGENTS.md ceremony-mode rendering, not about any
    particular client — and as of PRD-CORE-240-FR04 opencode no longer receives
    the shared AGENTS.md (it owns ``.opencode/INSTRUCTIONS.md``, referenced from
    ``opencode.json``).

    cursor-cli is the right substitute, not codex: these tests assert that
    ``ceremony_mode`` SWITCHES the rendered body between the full and minimal
    forms, and that switch lives on the GENERIC AGENTS.md render. codex always
    renders its own codex-specific section, so the mode has no effect there and
    the comparison would pass vacuously (both bodies identical). cursor-cli takes
    the generic path and its AGENTS.md is its primary carrier.
    """
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    (trw_dir / "patterns").mkdir(exist_ok=True)

    config = TRWConfig(
        trw_dir=str(trw_dir),
        ceremony_mode=ceremony_mode,
    )
    reader = FileStateReader()
    llm = MagicMock()
    llm.available = False

    with (
        patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
        patch("trw_mcp.state.claude_md._sync.recall_learnings", return_value=[]),
    ):
        return execute_claude_md_sync(
            scope="root",
            target_dir=None,
            config=config,
            reader=reader,
            llm=llm,
            client="cursor-cli",
        )
