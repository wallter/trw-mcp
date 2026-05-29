"""Analytics YAML fallback tests for reflection quality scoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._analytics_yaml_paths_support import _setup_trw, _write_entry, _writer
from trw_mcp.state.analytics import compute_reflection_quality


class TestComputeReflectionQualityYamlFallback:
    """Test compute_reflection_quality YAML fallback path."""

    def test_yaml_fallback_scans_entries(self, tmp_path: Path) -> None:
        """Lines 1202-1203, 1206-1219: SQLite fails, YAML scan counts metrics."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"
        reflections_dir = trw_dir / "reflections"

        _writer.write_yaml(
            reflections_dir / "ref-001.yaml",
            {"new_learnings": ["L-001", "L-002"]},
        )

        _write_entry(
            entries_dir,
            "entry-1",
            access_count=3,
            q_observations=2,
            tags=["testing", "coverage"],
            source_type="agent",
        )
        _write_entry(
            entries_dir,
            "entry-2",
            access_count=0,
            q_observations=0,
            tags=["architecture"],
            source_type="human",
        )
        _write_entry(
            entries_dir,
            "entry-3",
            status="resolved",
            access_count=1,
            q_observations=1,
            tags=["gotcha"],
            source_type="agent",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            with patch(
                "trw_mcp.state.memory_adapter.count_entries",
                side_effect=ImportError("no sqlite"),
            ):
                result = compute_reflection_quality(trw_dir)

        assert "score" in result
        assert 0.0 <= float(str(result["score"])) <= 1.0
        components = result.get("components", {})
        assert isinstance(components, dict)

    def test_yaml_fallback_with_rich_entries(self, tmp_path: Path) -> None:
        """Lines 1206-1219: YAML scan counts accessed, q_activated, tags, sources."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "e1",
            access_count=5,
            q_observations=3,
            tags=["testing", "fixtures", "mocking"],
            source_type="agent",
        )
        _write_entry(
            entries_dir,
            "e2",
            access_count=1,
            q_observations=0,
            tags=["architecture", "design"],
            source_type="human",
        )
        _write_entry(
            entries_dir,
            "e3",
            access_count=0,
            q_observations=1,
            tags=["gotcha", "pydantic", "config"],
            source_type="agent",
        )
        _write_entry(
            entries_dir,
            "e4",
            access_count=0,
            q_observations=0,
            tags=[],
            source_type="",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            with patch(
                "trw_mcp.state.memory_adapter.count_entries",
                side_effect=ImportError("no sqlite"),
            ):
                result = compute_reflection_quality(trw_dir)

        assert 0.0 <= float(str(result["score"])) <= 1.0
        diagnostics = result.get("diagnostics", {})
        assert isinstance(diagnostics, dict)
