"""Claude MD collection coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader

from ._prd_audit_claudemd_support import _reader, _writer


class TestCollectPromotableLearnings:
    """Cover lines 654, 660: q_value path and below-threshold filtering."""

    def test_uses_q_value_for_mature_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_promotable_learnings

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        entries_dir.mkdir(parents=True)

        # Entry with enough q_observations to use q_value
        _writer.write_yaml(
            entries_dir / "mature.yaml",
            {
                "id": "L-mature",
                "summary": "Mature learning",
                "status": "active",
                "impact": 0.3,  # below threshold
                "q_observations": config.q_cold_start_threshold,  # at threshold
                "q_value": 0.9,  # above threshold via q_value
            },
        )

        with pytest.warns(DeprecationWarning):
            result = collect_promotable_learnings(trw_dir, config, _reader)
        assert any(e.get("id") == "L-mature" for e in result)

    def test_filters_below_threshold(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_promotable_learnings

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        entries_dir.mkdir(parents=True)

        # Cold-start entry with low impact — should be excluded
        _writer.write_yaml(
            entries_dir / "low.yaml",
            {
                "id": "L-low",
                "summary": "Low impact learning",
                "status": "active",
                "impact": 0.3,  # below config.learning_promotion_impact = 0.7
                "q_observations": 0,
            },
        )

        with pytest.warns(DeprecationWarning):
            result = collect_promotable_learnings(trw_dir, config, _reader)
        assert all(e.get("id") != "L-low" for e in result)

    def test_skips_non_active_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_promotable_learnings

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        entries_dir.mkdir(parents=True)

        _writer.write_yaml(
            entries_dir / "obsolete.yaml",
            {
                "id": "L-obs",
                "summary": "Obsolete learning",
                "status": "obsolete",
                "impact": 0.9,
            },
        )

        with pytest.warns(DeprecationWarning):
            result = collect_promotable_learnings(trw_dir, config, _reader)
        assert all(e.get("id") != "L-obs" for e in result)

    def test_returns_empty_when_no_entries_dir(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_promotable_learnings

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No entries directory created

        with pytest.warns(DeprecationWarning):
            result = collect_promotable_learnings(trw_dir, config, _reader)
        assert result == []


class TestCollectPatterns:
    """Cover lines 673-674: pattern file reading."""

    def test_collects_pattern_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_patterns

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        patterns_dir = trw_dir / config.patterns_dir
        patterns_dir.mkdir(parents=True)

        _writer.write_yaml(
            patterns_dir / "wave-pattern.yaml",
            {
                "name": "Wave Pattern",
                "description": "Use waves for parallel execution",
            },
        )
        _writer.write_yaml(
            patterns_dir / "shard-pattern.yaml",
            {
                "name": "Shard Pattern",
                "description": "Decompose tasks by category",
            },
        )

        result = collect_patterns(trw_dir, config, _reader)
        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "Wave Pattern" in names

    def test_skips_index_yaml(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_patterns

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        patterns_dir = trw_dir / config.patterns_dir
        patterns_dir.mkdir(parents=True)

        _writer.write_yaml(patterns_dir / "index.yaml", {"total": 1})
        _writer.write_yaml(
            patterns_dir / "my-pattern.yaml",
            {
                "name": "My Pattern",
                "description": "Details",
            },
        )

        result = collect_patterns(trw_dir, config, _reader)
        assert len(result) == 1

    def test_returns_empty_when_no_patterns_dir(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_patterns

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        result = collect_patterns(trw_dir, config, _reader)
        assert result == []

    def test_skips_unreadable_pattern_files(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_patterns

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        patterns_dir = trw_dir / config.patterns_dir
        patterns_dir.mkdir(parents=True)

        _writer.write_yaml(patterns_dir / "good.yaml", {"name": "Good Pattern", "description": "Works"})
        _writer.write_yaml(patterns_dir / "also-good.yaml", {"name": "Also Good", "description": "Also works"})

        # Simulate a read error by using a mock reader that raises for one file
        mock_reader = MagicMock(spec=FileStateReader)
        call_count = 0

        def _selective_read(path: Path) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise StateError("read failed")
            return _reader.read_yaml(path)

        mock_reader.read_yaml.side_effect = _selective_read

        result = collect_patterns(trw_dir, config, mock_reader)
        # First file raises StateError → skipped; second file returned
        assert len(result) == 1


class TestCollectContextData:
    """Cover lines 704-705: exception handling in collect_context_data."""

    def test_reads_arch_and_conventions(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_context_data

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / config.context_dir
        context_dir.mkdir(parents=True)

        _writer.write_yaml(
            context_dir / "architecture.yaml",
            {
                "source_layout": "src/trw_mcp/",
            },
        )
        _writer.write_yaml(
            context_dir / "conventions.yaml",
            {
                "git_format": "feat(scope): msg",
            },
        )

        arch_data, conv_data = collect_context_data(trw_dir, config, _reader)
        assert arch_data.get("source_layout") == "src/trw_mcp/"
        assert conv_data.get("git_format") == "feat(scope): msg"

    def test_returns_empty_dicts_on_read_error(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import collect_context_data

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / config.context_dir
        context_dir.mkdir(parents=True)

        # Write a file that will cause a read error
        _writer.write_yaml(context_dir / "architecture.yaml", {"key": "value"})

        # Patch reader.read_yaml to raise StateError
        mock_reader = MagicMock(spec=FileStateReader)
        mock_reader.exists.return_value = True
        mock_reader.read_yaml.side_effect = StateError("read failed")

        arch_data, conv_data = collect_context_data(trw_dir, config, mock_reader)
        assert arch_data == {}
        assert conv_data == {}


class TestCollectPromotableLearningsExceptionContinue:
    """Cover claude_md.py lines 673-674: exception handling in collect_promotable_learnings."""

    def test_read_error_on_entry_file_is_skipped(self, tmp_path: Path) -> None:
        """Entry with unparseable q_observations raises ValueError and is skipped."""
        from unittest.mock import patch

        from trw_mcp.state.claude_md import collect_promotable_learnings

        config = TRWConfig()
        trw_dir = tmp_path / ".trw"

        # collect_promotable_learnings now reads from SQLite via list_active_learnings.
        # Patch it to return one good entry and one bad entry where q_observations
        # has a type that causes int() to raise (exercises the ValueError/TypeError
        # continue branch at lines 687-688).
        good_entry: dict[str, object] = {
            "id": "L-good",
            "summary": "Good learning",
            "status": "active",
            "impact": 0.9,
            "q_observations": 0,
        }
        bad_entry: dict[str, object] = {
            "id": "L-bad",
            "summary": "Bad learning",
            "status": "active",
            "impact": 0.9,
            # dict cannot be converted to int — triggers TypeError in the loop
            "q_observations": {"invalid": "value"},
        }

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=[good_entry, bad_entry],
        ):
            with pytest.warns(DeprecationWarning):
                result = collect_promotable_learnings(trw_dir, config, _reader)

        # bad entry should be skipped due to TypeError; good entry returned
        assert any(e.get("id") == "L-good" for e in result)
        assert all(e.get("id") != "L-bad" for e in result)
