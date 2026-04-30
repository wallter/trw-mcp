"""Shared helpers for core084 ceremony adaptation test splits."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig


def _run_agents_md_sync(
    tmp_path: Path,
    ceremony_mode: str = "full",
    agents_md_learning_injection: bool = False,
    agents_md_learning_max: int = 5,
    agents_md_learning_min_impact: float = 0.7,
    mock_learnings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Run execute_claude_md_sync with mocked infra targeting AGENTS.md."""
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
        agents_md_learning_injection=agents_md_learning_injection,
        agents_md_learning_max=agents_md_learning_max,
        agents_md_learning_min_impact=agents_md_learning_min_impact,
    )
    reader = FileStateReader()
    llm = MagicMock()
    llm.available = False

    recall_return = mock_learnings if mock_learnings is not None else []

    with (
        patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
        patch("trw_mcp.state.analytics.mark_promoted"),
        patch("trw_mcp.state.claude_md._sync.recall_learnings", return_value=recall_return),
    ):
        return execute_claude_md_sync(
            scope="root",
            target_dir=None,
            config=config,
            reader=reader,
            llm=llm,
            client="opencode",
        )
