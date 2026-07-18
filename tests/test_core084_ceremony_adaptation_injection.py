"""Tests for PRD-CORE-084 AGENTS.md learning injection behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._test_core084_ceremony_adaptation_support import _run_agents_md_sync
from trw_mcp.models.config import TRWConfig


class TestAgentsMdLearningInjection:
    """FR06: Inject high-impact learnings into AGENTS.md when enabled."""

    def test_injection_enabled_adds_key_learnings_section(self, tmp_path: Path) -> None:
        """When agents_md_learning_injection=True, AGENTS.md has Key Learnings section."""
        mock_learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "Always validate input types", "impact": 0.9},
            {"id": "L-002", "summary": "Use structured logging for debugging", "impact": 0.8},
        ]
        _run_agents_md_sync(
            tmp_path,
            agents_md_learning_injection=True,
            mock_learnings=mock_learnings,
        )

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" in content
        assert "Always validate input types" in content
        assert "Use structured logging for debugging" in content

    def test_injection_disabled_no_key_learnings(self, tmp_path: Path) -> None:
        """When agents_md_learning_injection=False, AGENTS.md has no Key Learnings."""
        _run_agents_md_sync(tmp_path, agents_md_learning_injection=False)

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" not in content

    def test_injection_with_empty_learnings(self, tmp_path: Path) -> None:
        """When no learnings qualify, Key Learnings section is not added."""
        _run_agents_md_sync(
            tmp_path,
            agents_md_learning_injection=True,
            mock_learnings=[],
        )

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" not in content

    def test_injection_sanitizes_summaries(self, tmp_path: Path) -> None:
        """Learning summaries are sanitized (no markdown links, HTML, URLs)."""
        learnings_with_links: list[dict[str, object]] = [
            {
                "id": "L-010",
                "summary": "Check [docs](https://example.com) for details",
                "impact": 0.9,
            },
        ]
        _run_agents_md_sync(
            tmp_path,
            agents_md_learning_injection=True,
            mock_learnings=learnings_with_links,
        )

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" in content
        assert "https://example.com" not in content
        assert "Check docs for details" in content

    def test_injection_works_with_light_mode(self, tmp_path: Path) -> None:
        """Learning injection works with light ceremony mode."""
        mock_learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "Test learning", "impact": 0.9},
        ]
        _run_agents_md_sync(
            tmp_path,
            ceremony_mode="light",
            agents_md_learning_injection=True,
            mock_learnings=mock_learnings,
        )

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" in content
        assert "## Workflow" not in content

    def test_injection_fail_open(self, tmp_path: Path) -> None:
        """If learning query fails, AGENTS.md renders without learnings (fail-open)."""
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
            agents_md_learning_injection=True,
        )
        reader = FileStateReader()
        llm = MagicMock()
        llm.available = False

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch(
                "trw_mcp.state.claude_md._sync.recall_learnings",
                side_effect=RuntimeError("DB error"),
            ),
        ):
            result = execute_claude_md_sync(
                scope="root",
                target_dir=None,
                config=config,
                reader=reader,
                llm=llm,
                client="opencode",
            )

        assert result["agents_md_synced"] is True
        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Key Learnings" not in content

    def test_config_defaults_for_learning_injection(self) -> None:
        """Default config values for AGENTS.md learning injection fields."""
        config = TRWConfig()
        assert config.agents_md_learning_injection is True
        assert config.agents_md_learning_max == 5
        assert config.agents_md_learning_min_impact == 0.7
