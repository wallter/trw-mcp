"""Tests for trw_mcp.state.recall_search — hybrid search path coverage.

Targets lines 62-93 in recall_search.py: the dense vector / hybrid search
path that was previously at 0% coverage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import search_entries

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entries_dir(tmp_path: Path) -> Path:
    """Create an entries directory with a few YAML files for testing."""
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()
    return entries_dir


def _write_entry(writer: FileStateWriter, entries_dir: Path, entry: dict) -> Path:
    """Write a learning entry YAML file and return the path."""
    entry_id = entry.get("id", "unknown")
    fname = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(fname, entry)
    return fname


# ===========================================================================
# Hybrid search path tests (lines 53-93)
# ===========================================================================


class TestHybridSearchHappyPath:
    """Cover lines 62-91: hybrid_search returns results that pass filters."""

    def test_hybrid_results_returned_directly(self, tmp_path: Path) -> None:
        """When hybrid_search returns results, they are returned without
        falling through to keyword scan (lines 60-91)."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        # Create a real file so the glob finds it
        _write_entry(writer, entries_dir, {
            "id": "L-abc123",
            "summary": "test learning",
            "detail": "detail here",
            "impact": 0.8,
            "status": "active",
            "tags": ["testing"],
        })

        hybrid_results = [
            {
                "id": "L-abc123",
                "summary": "test learning",
                "detail": "detail here",
                "impact": 0.8,
                "status": "active",
                "tags": ["testing"],
            }
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["test"],
                reader=reader,
            )

        assert len(matches) == 1
        assert matches[0]["id"] == "L-abc123"
        # File should be resolved from glob
        assert len(matched_files) == 1
        assert "L-abc123" in matched_files[0].name

    def test_hybrid_path_with_module_patch(self, tmp_path: Path) -> None:
        """Properly patch the function-local import to exercise lines 53-91."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        _write_entry(writer, entries_dir, {
            "id": "L-test01",
            "summary": "hybrid result",
            "detail": "found via vector",
            "impact": 0.9,
            "status": "active",
            "tags": ["architecture"],
        })

        hybrid_results = [
            {
                "id": "L-test01",
                "summary": "hybrid result",
                "detail": "found via vector",
                "impact": 0.9,
                "status": "active",
                "tags": ["architecture"],
            }
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)

        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["hybrid"],
                reader=reader,
            )

        assert len(matches) == 1
        assert matches[0]["id"] == "L-test01"
        assert len(matched_files) == 1


class TestHybridSearchFiltering:
    """Cover filter branches inside the hybrid results loop (lines 62-84)."""

    def _run_hybrid_search(
        self,
        tmp_path: Path,
        hybrid_results: list[dict],
        *,
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = None,
        create_files: bool = True,
    ) -> tuple[list[dict], list[Path]]:
        """Helper: run search_entries with mocked hybrid_search."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        if create_files:
            for entry in hybrid_results:
                entry_id = entry.get("id", "")
                if entry_id:
                    _write_entry(writer, entries_dir, entry)

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            return search_entries(
                entries_dir,
                query_tokens=["query"],
                reader=reader,
                tags=tags,
                min_impact=min_impact,
                status=status,
            )

    def test_min_impact_filters_low_impact(self, tmp_path: Path) -> None:
        """Entries below min_impact are excluded (line 66-67)."""
        results = [
            {"id": "L-low", "summary": "low", "detail": "", "impact": 0.2, "tags": []},
            {"id": "L-high", "summary": "high", "detail": "", "impact": 0.9, "tags": []},
        ]
        matches, _ = self._run_hybrid_search(tmp_path, results, min_impact=0.5)
        ids = [m["id"] for m in matches]
        assert "L-high" in ids
        assert "L-low" not in ids

    def test_status_filter_excludes_non_matching(self, tmp_path: Path) -> None:
        """Entries whose status != filter are excluded (lines 68-71)."""
        results = [
            {"id": "L-act", "summary": "active", "detail": "", "impact": 0.5, "status": "active", "tags": []},
            {"id": "L-res", "summary": "resolved", "detail": "", "impact": 0.5, "status": "resolved", "tags": []},
        ]
        matches, _ = self._run_hybrid_search(tmp_path, results, status="active")
        ids = [m["id"] for m in matches]
        assert "L-act" in ids
        assert "L-res" not in ids

    def test_status_filter_defaults_to_active(self, tmp_path: Path) -> None:
        """Entries without explicit status default to 'active' (line 69)."""
        results = [
            {"id": "L-nostatus", "summary": "no status", "detail": "", "impact": 0.5, "tags": []},
        ]
        matches, _ = self._run_hybrid_search(tmp_path, results, status="active")
        assert len(matches) == 1
        assert matches[0]["id"] == "L-nostatus"

    def test_tag_filter_excludes_non_matching(self, tmp_path: Path) -> None:
        """Entries without matching tags are excluded (lines 72-74)."""
        results = [
            {"id": "L-py", "summary": "python", "detail": "", "impact": 0.5, "tags": ["python"]},
            {"id": "L-js", "summary": "javascript", "detail": "", "impact": 0.5, "tags": ["javascript"]},
        ]
        matches, _ = self._run_hybrid_search(tmp_path, results, tags=["python"])
        ids = [m["id"] for m in matches]
        assert "L-py" in ids
        assert "L-js" not in ids

    def test_tag_filter_with_non_list_tags_passes(self, tmp_path: Path) -> None:
        """When entry tags is not a list, the tag filter is skipped (line 72 isinstance check)."""
        results = [
            {"id": "L-str", "summary": "string tags", "detail": "", "impact": 0.5, "tags": "not-a-list"},
        ]
        # With tag filter active but tags is a string (not list), entry should NOT be excluded
        matches, _ = self._run_hybrid_search(tmp_path, results, tags=["anything"])
        assert len(matches) == 1

    def test_entry_id_not_in_glob_uses_fallback_path(self, tmp_path: Path) -> None:
        """When entry_id doesn't match any file, fallback path is used (lines 83-84)."""
        # Do NOT create a file for this entry so glob won't find it
        results = [
            {"id": "L-missing", "summary": "missing file", "detail": "", "impact": 0.5, "tags": []},
        ]
        matches, matched_files = self._run_hybrid_search(
            tmp_path, results, create_files=False,
        )
        assert len(matches) == 1
        assert len(matched_files) == 1
        # Fallback path uses entries_dir / f"{entry_id}.yaml"
        assert matched_files[0].name == "L-missing.yaml"

    def test_entry_without_id_no_file_tracked(self, tmp_path: Path) -> None:
        """When entry has empty id, no file is added to matched_files (line 78)."""
        results = [
            {"id": "", "summary": "no id", "detail": "", "impact": 0.5, "tags": []},
        ]
        matches, matched_files = self._run_hybrid_search(
            tmp_path, results, create_files=False,
        )
        assert len(matches) == 1
        assert len(matched_files) == 0

    def test_entry_with_no_id_key_no_file_tracked(self, tmp_path: Path) -> None:
        """When entry has no 'id' key at all, no file is added."""
        results = [
            {"summary": "no id key", "detail": "", "impact": 0.5, "tags": []},
        ]
        matches, matched_files = self._run_hybrid_search(
            tmp_path, results, create_files=False,
        )
        assert len(matches) == 1
        assert len(matched_files) == 0

    def test_multiple_entries_mixed_filters(self, tmp_path: Path) -> None:
        """Multiple entries with different filter outcomes (lines 62-84)."""
        results = [
            {"id": "L-pass", "summary": "pass all", "detail": "", "impact": 0.8, "status": "active", "tags": ["python"]},
            {"id": "L-low", "summary": "low impact", "detail": "", "impact": 0.1, "status": "active", "tags": ["python"]},
            {"id": "L-wrong-status", "summary": "wrong status", "detail": "", "impact": 0.8, "status": "resolved", "tags": ["python"]},
            {"id": "L-wrong-tag", "summary": "wrong tag", "detail": "", "impact": 0.8, "status": "active", "tags": ["javascript"]},
        ]
        matches, _ = self._run_hybrid_search(
            tmp_path, results, min_impact=0.5, status="active", tags=["python"],
        )
        ids = [m["id"] for m in matches]
        assert ids == ["L-pass"]


class TestHybridSearchExceptionFallback:
    """Cover lines 92-93: exception in hybrid search falls through to keyword scan."""

    def test_hybrid_import_error_falls_to_keyword(self, tmp_path: Path) -> None:
        """When hybrid_search import fails, keyword scan is used (lines 92-93)."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        _write_entry(writer, entries_dir, {
            "id": "L-keyword",
            "summary": "keyword fallback result",
            "detail": "found via keywords",
            "impact": 0.7,
            "tags": [],
        })

        # Make the retrieval module import raise ImportError
        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(side_effect=ImportError("no retrieval"))

        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["keyword", "fallback"],
                reader=reader,
            )

        # Should find the entry via keyword scan
        assert len(matches) == 1
        assert matches[0]["id"] == "L-keyword"
        assert len(matched_files) == 1

    def test_hybrid_runtime_error_falls_to_keyword(self, tmp_path: Path) -> None:
        """When hybrid_search raises RuntimeError, keyword scan is used."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        _write_entry(writer, entries_dir, {
            "id": "L-fallback",
            "summary": "runtime error fallback",
            "detail": "found via keywords after error",
            "impact": 0.6,
            "tags": [],
        })

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(side_effect=RuntimeError("broken"))

        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, _ = search_entries(
                entries_dir,
                query_tokens=["runtime", "error", "fallback"],
                reader=reader,
            )

        assert len(matches) == 1
        assert matches[0]["id"] == "L-fallback"

    def test_get_config_error_falls_to_keyword(self, tmp_path: Path) -> None:
        """When get_config() raises, keyword scan is used."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        _write_entry(writer, entries_dir, {
            "id": "L-cfg",
            "summary": "config error fallback",
            "detail": "config error forces keyword path",
            "impact": 0.5,
            "tags": [],
        })

        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(side_effect=RuntimeError("config broken"))

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.models.config": mock_config_mod,
                "trw_mcp.state.retrieval": MagicMock(),
            },
        ):
            matches, _ = search_entries(
                entries_dir,
                query_tokens=["config", "error", "fallback"],
                reader=reader,
            )

        assert len(matches) == 1
        assert matches[0]["id"] == "L-cfg"


class TestHybridSearchEmptyResults:
    """Cover the branch where hybrid_search returns empty results."""

    def test_empty_hybrid_results_falls_to_keyword(self, tmp_path: Path) -> None:
        """When hybrid_search returns empty list, keyword scan takes over (line 60)."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        _write_entry(writer, entries_dir, {
            "id": "L-kw",
            "summary": "keyword only result",
            "detail": "hybrid returned empty",
            "impact": 0.5,
            "tags": [],
        })

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=[])

        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, _ = search_entries(
                entries_dir,
                query_tokens=["keyword", "only"],
                reader=reader,
            )

        assert len(matches) == 1
        assert matches[0]["id"] == "L-kw"


class TestHybridSearchFileResolution:
    """Cover the file resolution logic in the hybrid path (lines 78-84)."""

    def test_entry_id_matches_file_via_glob(self, tmp_path: Path) -> None:
        """When entry ID matches a file found by glob, that file is used (line 80-82)."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        # Create file with a longer name containing the ID
        real_file = entries_dir / "2026-01-01-L-found123-some-slug.yaml"
        writer.write_yaml(real_file, {"id": "L-found123", "summary": "found"})

        hybrid_results = [
            {"id": "L-found123", "summary": "found", "detail": "", "impact": 0.5, "tags": []},
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["found"],
                reader=reader,
            )

        assert len(matched_files) == 1
        assert matched_files[0] == real_file

    def test_entry_id_no_file_match_uses_fallback(self, tmp_path: Path) -> None:
        """When no file contains the entry ID, a synthetic path is used (lines 83-84)."""
        entries_dir = _make_entries_dir(tmp_path)
        reader = FileStateReader()

        # No files exist at all in entries_dir
        hybrid_results = [
            {"id": "L-ghost", "summary": "ghost", "detail": "", "impact": 0.5, "tags": []},
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["ghost"],
                reader=reader,
            )

        assert len(matched_files) == 1
        assert matched_files[0] == entries_dir / "L-ghost.yaml"

    def test_multiple_entries_mixed_file_resolution(self, tmp_path: Path) -> None:
        """Mix of found and not-found files in a single hybrid batch."""
        entries_dir = _make_entries_dir(tmp_path)
        writer = FileStateWriter()
        reader = FileStateReader()

        # Only create a file for L-found
        real_file = entries_dir / "L-found.yaml"
        writer.write_yaml(real_file, {"id": "L-found", "summary": "f"})

        hybrid_results = [
            {"id": "L-found", "summary": "found", "detail": "", "impact": 0.5, "tags": []},
            {"id": "L-notfound", "summary": "not found", "detail": "", "impact": 0.5, "tags": []},
            {"id": "", "summary": "no id", "detail": "", "impact": 0.5, "tags": []},
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, matched_files = search_entries(
                entries_dir,
                query_tokens=["test"],
                reader=reader,
            )

        assert len(matches) == 3
        # L-found: real file, L-notfound: synthetic path, "": no file
        assert len(matched_files) == 2
        file_names = [f.name for f in matched_files]
        assert "L-found.yaml" in file_names
        assert "L-notfound.yaml" in file_names


class TestHybridSearchImpactCasting:
    """Cover the impact float casting edge case (line 65)."""

    def test_impact_as_string_is_cast_to_float(self, tmp_path: Path) -> None:
        """Impact stored as a string is properly cast (line 65)."""
        entries_dir = _make_entries_dir(tmp_path)
        reader = FileStateReader()

        hybrid_results = [
            {"id": "L-str", "summary": "string impact", "detail": "", "impact": "0.8", "tags": []},
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, _ = search_entries(
                entries_dir,
                query_tokens=["string"],
                reader=reader,
                min_impact=0.5,
            )

        assert len(matches) == 1

    def test_impact_missing_defaults_to_zero(self, tmp_path: Path) -> None:
        """Missing impact field defaults to 0.0 (line 64)."""
        entries_dir = _make_entries_dir(tmp_path)
        reader = FileStateReader()

        hybrid_results = [
            {"id": "L-noimp", "summary": "no impact", "detail": "", "tags": []},
        ]

        mock_retrieval = MagicMock()
        mock_retrieval.hybrid_search = MagicMock(return_value=hybrid_results)
        mock_config_mod = MagicMock()
        mock_config_mod.get_config = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "trw_mcp.state.retrieval": mock_retrieval,
                "trw_mcp.models.config": mock_config_mod,
            },
        ):
            matches, _ = search_entries(
                entries_dir,
                query_tokens=["no"],
                reader=reader,
                min_impact=0.5,
            )

        # Default impact 0.0 < 0.5 min_impact, so filtered out
        assert len(matches) == 0
