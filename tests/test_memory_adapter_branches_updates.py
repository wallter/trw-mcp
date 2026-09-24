"""Targeted memory adapter update and path branch tests.

PRD-CORE-280 slice e1: ``TestRecallLearningsStatusParsing`` and
``TestUpdateLearningBranches`` route through ``fake_memory_store``; the recall
case seeds the fake directly at the ``"default"`` namespace the fake's
``recall()`` searches (``store_learning`` itself writes under the fixture's
``FAKE_NAMESPACE``, which the fake's ``recall()`` does not consult — see
``tests/test_memory_adapter_wildcard_ranking.py`` for the same workaround).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.memory_adapter import (
    find_yaml_path_for_entry,
    recall_learnings,
    store_learning,
    update_learning,
)

from ._memory_adapter_branches_support import trw_dir  # noqa: F401
from ._memory_store_fake import FakeMemoryStore


class TestRecallLearningsStatusParsing:
    def test_invalid_status_string_ignored(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Invalid status string is silently ignored (lines 531-534)."""
        fake_memory_store.put("Status test", "default", {"entry_id": "L-is1", "detail": "d"})
        results = recall_learnings(trw_dir, "*", status="bogus_status")
        assert [r["id"] for r in results] == ["L-is1"], "an invalid status must not filter the row out"

    def test_valid_status_active(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Valid status='active' filters correctly."""
        fake_memory_store.put("Active entry", "default", {"entry_id": "L-va1", "detail": "d"})
        results = recall_learnings(trw_dir, "*", status="active")
        assert len(results) >= 1


class TestUpdateLearningBranches:
    def test_detail_update(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """detail= kwarg updates the detail field (lines 599-600)."""
        store_learning(trw_dir, "L-du1", "Summary", "Old detail")
        result = update_learning(trw_dir, "L-du1", detail="New detail")
        assert result["status"] == "updated"
        assert "detail updated" in result["changes"]

    def test_summary_update(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """summary= kwarg updates the content field (lines 603-604)."""
        store_learning(trw_dir, "L-su1", "Old Summary", "d")
        result = update_learning(trw_dir, "L-su1", summary="New Summary")
        assert result["status"] == "updated"
        assert "summary updated" in result["changes"]

    def test_impact_out_of_range(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Impact outside [0.0, 1.0] returns invalid (line 608)."""
        store_learning(trw_dir, "L-ir1", "s", "d")
        result = update_learning(trw_dir, "L-ir1", impact=1.5)
        assert result["status"] == "invalid"
        assert result["error"].startswith("Invalid impact 1.5")

    def test_impact_negative(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Negative impact returns invalid."""
        store_learning(trw_dir, "L-ir2", "s", "d")
        result = update_learning(trw_dir, "L-ir2", impact=-0.1)
        assert result["status"] == "invalid"


class TestFindYamlPathIndexSkip:
    def test_skips_index_yaml_returns_none(self, tmp_path: Path) -> None:
        """index.yaml is skipped; when it is the only file, returns None (line 710)."""
        from trw_mcp.models.config import get_config as _get_config

        trw = tmp_path / ".trw"
        trw.mkdir()
        cfg = _get_config()
        entries_dir = trw / cfg.learnings_dir / cfg.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        (entries_dir / "index.yaml").write_text("index: true\n")

        result = find_yaml_path_for_entry(trw, "L-nomatch")
        assert result is None

    def test_entries_dir_missing_returns_none(self, tmp_path: Path) -> None:
        """When entries dir does not exist, returns None (line 698)."""
        trw = tmp_path / ".trw"
        trw.mkdir()
        result = find_yaml_path_for_entry(trw, "L-any")
        assert result is None

    def test_partial_match_with_index_yaml_present(self, trw_dir: Path) -> None:
        """Partial match works when index.yaml is also present."""
        from trw_mcp.models.config import get_config as _get_config

        cfg = _get_config()
        entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        (entries_dir / "index.yaml").write_text("index: true\n")
        (entries_dir / "2026-01-01-L-idx001-some-summary.yaml").write_text("id: L-idx001\n")

        result = find_yaml_path_for_entry(trw_dir, "L-idx001")
        assert result is not None
        assert result.name != "index.yaml"
        assert "L-idx001" in result.name
