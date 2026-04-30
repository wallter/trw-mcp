"""Split recall_search coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import collect_context, search_patterns


class TestSearchPatternsExceptionHandling:
    """Cover exception handling and skip paths in search_patterns."""

    def test_index_yaml_is_skipped(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """index.yaml is always skipped (line 119 continue branch)."""
        patterns_dir = tmp_path / "patterns"
        patterns_dir.mkdir()
        writer.write_yaml(
            patterns_dir / "index.yaml",
            {
                "name": "should not appear",
                "description": "skipped",
            },
        )
        writer.write_yaml(
            patterns_dir / "actual.yaml",
            {
                "name": "real pattern",
                "description": "this should appear",
            },
        )

        matches = search_patterns(patterns_dir, query_tokens=[], reader=reader)
        names = [str(m.get("name", "")) for m in matches]
        assert "real pattern" in names
        assert "should not appear" not in names

    def test_corrupt_pattern_file_is_skipped(
        self, tmp_path: Path, reader: FileStateReader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateError/ValueError on pattern read causes that file to be skipped (lines 127-128)."""
        del monkeypatch
        patterns_dir = tmp_path / "patterns"
        patterns_dir.mkdir()
        (patterns_dir / "bad.yaml").write_text("{invalid", encoding="utf-8")
        (patterns_dir / "good.yaml").write_text("name: good\ndescription: works\n", encoding="utf-8")

        matches = search_patterns(patterns_dir, query_tokens=[], reader=reader)
        names = [str(m.get("name", "")) for m in matches]
        assert "good" in names


class TestCollectContextConventions:
    """Cover collect_context with conventions.yaml present."""

    def test_collects_conventions_when_present(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When conventions.yaml exists, it is included in context (line 189)."""
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        writer.write_yaml(
            context_dir / "conventions.yaml",
            {
                "naming": "snake_case",
                "indent": 4,
            },
        )

        result = collect_context(trw_dir, "context", reader)
        assert "conventions" in result
        assert isinstance(result["conventions"], dict)

    def test_collects_both_when_both_present(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Both architecture and conventions are collected when both exist."""
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        writer.write_yaml(context_dir / "architecture.yaml", {"layers": ["tool", "state"]})
        writer.write_yaml(context_dir / "conventions.yaml", {"style": "pep8"})

        result = collect_context(trw_dir, "context", reader)
        assert "architecture" in result
        assert "conventions" in result

    def test_returns_empty_when_neither_exists(self, tmp_path: Path, reader: FileStateReader) -> None:
        """No context files -> empty dict."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        result = collect_context(trw_dir, "context", reader)
        assert result == {}


class TestSearchPatternsNonExistentDir:
    """Cover search_patterns early return when dir missing."""

    def test_nonexistent_patterns_dir_returns_empty(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Non-existent patterns_dir returns [] immediately (line 115 branch)."""
        result = search_patterns(tmp_path / "no_patterns", query_tokens=[], reader=reader)
        assert result == []
