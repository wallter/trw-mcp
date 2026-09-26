"""Edge-case embedding and store wiring tests for state/memory_adapter.py.

What ``store_learning`` embeds is the daemon's: ``memory_store_impl`` encodes
``"<content> <detail>"`` (``trw-memory/tests/test_hybrid_runtime_wiring.py``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.memory_adapter import (
    find_entry_by_id,
    store_learning,
)

from ._memory_adapter_edge_support import trw_dir  # noqa: F401
from ._memory_store_fake import FakeMemoryStore


class TestStoreLearningTagInference:
    def test_inferred_tags_are_appended(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """store_learning appends inferred topic tags to user-provided tags."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["inferred-tag"],
        ):
            store_learning(trw_dir, "L-ti1", "Summary about Python", "d", tags=["user-tag"])

        entry = find_entry_by_id(trw_dir, "L-ti1")
        assert entry is not None
        tags = entry["tags"]
        assert isinstance(tags, list)
        assert "user-tag" in tags
        assert "inferred-tag" in tags

    def test_no_inferred_tags_keeps_original(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """When infer_topic_tags returns empty, original tags are preserved."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=[],
        ):
            store_learning(trw_dir, "L-ti2", "Summary", "d", tags=["original"])

        entry = find_entry_by_id(trw_dir, "L-ti2")
        assert entry is not None
        assert "original" in entry["tags"]

    def test_none_tags_with_inference(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """When user provides no tags, inferred tags are the only tags."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["auto-tag"],
        ):
            store_learning(trw_dir, "L-ti3", "Summary", "d", tags=None)

        entry = find_entry_by_id(trw_dir, "L-ti3")
        assert entry is not None
        assert "auto-tag" in entry["tags"]
