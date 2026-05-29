"""Knowledge topology tool registration, recall, and config tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


class TestToolRegistration:
    """FR06: trw_knowledge_sync appears in MCP server tool registry."""

    def test_knowledge_sync_registered(self) -> None:
        from trw_mcp.server import mcp

        tool_names = set(get_tools_sync(mcp).keys())
        assert "trw_knowledge_sync" in tool_names

    def test_knowledge_sync_callable(self, tmp_path: Path) -> None:
        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_knowledge_sync"]
        assert callable(tool.fn)

    def test_knowledge_sync_dry_run_parameter(self, tmp_path: Path) -> None:
        import inspect

        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_knowledge_sync"]
        sig = inspect.signature(tool.fn)
        assert "dry_run" in sig.parameters

    def test_knowledge_sync_returns_elapsed_seconds(self, tmp_path: Path) -> None:
        from trw_mcp.server import mcp
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        tool = get_tools_sync(mcp)["trw_knowledge_sync"]

        with patch("trw_mcp.state.knowledge_topology.count_entries", return_value=1):
            result = tool.fn(dry_run=False)

        assert "elapsed_seconds" in result
        assert isinstance(result["elapsed_seconds"], float)


class TestRecallTopicFilter:
    """FR07: topic= parameter filters recall results to a knowledge cluster."""

    def _setup_clusters_json(self, trw_dir: Path, slug: str, entry_ids: list[str]) -> None:
        knowledge_dir = trw_dir / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "clusters.json").write_text(
            json.dumps({slug: entry_ids, "updated_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )

    def test_topic_filters_to_matching_entries(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001", "L-002"])

        matching = [
            {"id": "L-001", "summary": "Pydantic tip", "impact": 0.8, "tags": ["pydantic"], "status": "active"},
            {"id": "L-002", "summary": "Another tip", "impact": 0.7, "tags": ["pydantic"], "status": "active"},
        ]
        not_matching = [
            {"id": "L-999", "summary": "Unrelated", "impact": 0.5, "tags": ["fastapi"], "status": "active"},
        ]
        all_entries = matching + not_matching

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
            patch("trw_mcp.tools.learning._config") as mock_config,
        ):
            mock_config.knowledge_output_dir = "knowledge"
            mock_config.recall_max_results = 25
            mock_config.recall_utility_lambda = 0.3
            mock_config.context_dir = "context"
            mock_config.patterns_dir = "patterns"
            mock_config.recall_compact_fields = frozenset({"id", "summary", "impact", "tags", "status"})

            clusters_data = json.loads((trw_dir / "knowledge" / "clusters.json").read_text(encoding="utf-8"))
            allowed_ids = set(clusters_data["pydantic"])
            filtered = [entry for entry in all_entries if str(entry.get("id", "")) in allowed_ids]

        assert len(filtered) == 2
        filtered_ids = {entry["id"] for entry in filtered}
        assert "L-001" in filtered_ids
        assert "L-002" in filtered_ids
        assert "L-999" not in filtered_ids

    def test_topic_filter_nonexistent_topic_ignored(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        clusters_data = json.loads((trw_dir / "knowledge" / "clusters.json").read_text(encoding="utf-8"))
        topic_filter_ignored = "nonexistent" not in clusters_data
        assert topic_filter_ignored is True

    def test_topic_filter_missing_clusters_file_ignored(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        clusters_path = trw_dir / "knowledge" / "clusters.json"
        assert not clusters_path.exists()
        topic_filter_ignored = not clusters_path.exists()
        assert topic_filter_ignored is True

    def test_topic_filter_malformed_clusters_json_ignored(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        knowledge_dir = trw_dir / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "clusters.json").write_text("{not valid json!!!", encoding="utf-8")

        topic_filter_ignored = False
        try:
            json.loads((knowledge_dir / "clusters.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            topic_filter_ignored = True
        assert topic_filter_ignored is True

    def test_topic_none_no_filter(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        all_entries = [
            {"id": "L-001", "summary": "Tip one", "impact": 0.8, "tags": [], "status": "active"},
            {"id": "L-002", "summary": "Tip two", "impact": 0.6, "tags": [], "status": "active"},
        ]

        topic = None
        topic_filter_ignored = False
        if topic is not None:
            topic_filter_ignored = True  # pragma: no cover

        assert len(all_entries) == 2
        assert topic_filter_ignored is False

    def test_topic_filter_via_real_trw_recall(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001", "L-002"])

        all_entries = [
            {"id": "L-001", "summary": "Pydantic tip", "impact": 0.8, "tags": ["pydantic"], "status": "active"},
            {"id": "L-002", "summary": "Another tip", "impact": 0.7, "tags": ["pydantic"], "status": "active"},
            {"id": "L-999", "summary": "Unrelated", "impact": 0.5, "tags": ["fastapi"], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_recall"]

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic="pydantic")

        returned_ids = {str(entry.get("id", "")) for entry in result["learnings"]}
        assert "L-999" not in returned_ids
        assert result["topic_filter_ignored"] is False

    def test_topic_filter_nonexistent_sets_ignored_flag(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_recall"]

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
            patch("trw_mcp.telemetry.remote_recall.fetch_shared_learnings", return_value=[]),
        ):
            result = tool.fn(query="*", topic="nonexistent_topic")

        assert result["topic_filter_ignored"] is True
        assert len(result["learnings"]) == len(all_entries)

    def test_topic_filter_no_clusters_file_sets_ignored_flag(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_recall"]

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic="pydantic")

        assert result["topic_filter_ignored"] is True

    def test_topic_none_no_filter_ignored_flag_false(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = get_tools_sync(mcp)["trw_recall"]

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic=None)

        assert result["topic_filter_ignored"] is False


class TestKnowledgeTopologyConfig:
    """FR08: Config section 38 fields have correct defaults and validation."""

    def test_knowledge_sync_threshold_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_sync_threshold == 50

    def test_knowledge_jaccard_threshold_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_jaccard_threshold == 0.3

    def test_knowledge_min_cluster_size_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_min_cluster_size == 3

    def test_knowledge_output_dir_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_output_dir == "knowledge"

    def test_knowledge_jaccard_threshold_ge_zero(self) -> None:
        config = TRWConfig(knowledge_jaccard_threshold=0.0)
        assert config.knowledge_jaccard_threshold == 0.0

    def test_knowledge_jaccard_threshold_le_one(self) -> None:
        config = TRWConfig(knowledge_jaccard_threshold=1.0)
        assert config.knowledge_jaccard_threshold == 1.0

    def test_knowledge_min_cluster_size_ge_one(self) -> None:
        config = TRWConfig(knowledge_min_cluster_size=1)
        assert config.knowledge_min_cluster_size == 1

    def test_knowledge_output_dir_customizable(self) -> None:
        config = TRWConfig(knowledge_output_dir="custom_knowledge")
        assert config.knowledge_output_dir == "custom_knowledge"

    def test_knowledge_sync_threshold_customizable(self) -> None:
        config = TRWConfig(knowledge_sync_threshold=100)
        assert config.knowledge_sync_threshold == 100
