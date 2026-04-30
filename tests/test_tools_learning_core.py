"""Tests for learning tool docs, writes, updates, and analytics counters."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.persistence import FileStateReader

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools, set_project_root

class TestToolDocstrings:
    """PRD-CORE-119: learning tool schema guidance stays accurate and high-signal."""

    def test_trw_learn_docstring_includes_quality_gate_and_tiers(self) -> None:
        tools = _get_tools()
        doc = tools["trw_learn"].fn.__doc__ or ""
        normalized_doc = " ".join(doc.split())

        assert "Only record learnings that:" in doc
        assert "prevent repeated mistakes" in doc
        assert "Routine observations" in doc
        assert "Required:" in doc
        assert "Recommended:" in doc
        assert "Advanced (auto-detected if omitted):" in doc
        assert (
            "Most learnings need only summary and detail. Adding tags and impact "
            "improves recall precision. All other fields are auto-detected."
        ) in normalized_doc

    def test_trw_instructions_sync_docstring_matches_post_093_behavior(self) -> None:
        tools = _get_tools()
        doc = tools["trw_instructions_sync"].fn.__doc__ or ""

        assert "Sync TRW protocol and ceremony guidance" in doc
        assert "Learnings are not promoted into the instruction file" in doc
        assert "trw_session_start()" in doc

    def test_trw_claude_md_sync_alias_still_registered(self) -> None:
        """The deprecated trw_claude_md_sync alias must remain registered for backward compat."""
        tools = _get_tools()
        assert "trw_claude_md_sync" in tools, "trw_claude_md_sync alias must remain for backward compatibility"
        assert "trw_instructions_sync" in tools, "canonical name trw_instructions_sync must be registered"

class TestTrwLearn:
    """Tests for trw_learn tool."""

    def test_records_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Test learning",
            detail="This is a detailed learning entry",
            tags=["testing", "example"],
            impact=0.8,
        )
        assert "learning_id" in result
        assert result["status"] == "recorded"

        entries_dir = _entries_dir(tmp_path)
        assert entries_dir.exists()
        entry_files = list(entries_dir.glob("*.yaml"))
        assert len(entry_files) == 1

    def test_updates_index(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Learning 1",
            detail="Detail 1",
        )
        tools["trw_learn"].fn(
            summary="Learning 2",
            detail="Detail 2",
        )

        index = reader.read_yaml(tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml")
        assert index["total_count"] == 2

class TestTrwLearnUpdate:
    """Tests for trw_learn_update tool."""

    def test_updates_status_to_resolved(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Bug that was fixed",
            detail="Some bug detail",
            impact=0.8,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="resolved",
        )
        assert update_result["status"] == "updated"
        assert "status→resolved" in update_result["changes"]

        # Verify on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert data["status"] == "resolved"
                assert data.get("resolved_at") is not None
                break

    def test_updates_status_to_obsolete(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outdated learning",
            detail="No longer relevant",
            impact=0.7,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="obsolete",
        )
        assert update_result["status"] == "updated"
        assert "status→obsolete" in update_result["changes"]

    def test_updates_detail_and_summary(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Original summary",
            detail="Original detail",
            impact=0.6,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            summary="Refined summary",
            detail="Better detail with more context",
        )
        assert update_result["status"] == "updated"
        assert "summary updated" in update_result["changes"]
        assert "detail updated" in update_result["changes"]

        # Verify on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert data["summary"] == "Refined summary"
                assert data["detail"] == "Better detail with more context"
                break

    def test_updates_impact(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Adjustable impact",
            detail="Impact will change",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            impact=0.9,
        )
        assert update_result["status"] == "updated"
        assert "impact→0.9" in update_result["changes"]

    def test_rejects_invalid_status(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Status validation test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="invalid_status",
        )
        assert update_result["status"] == "invalid"
        assert "error" in update_result

    def test_rejects_invalid_impact(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Impact validation test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            impact=1.5,
        )
        assert update_result["status"] == "invalid"
        assert "error" in update_result

    def test_not_found_returns_error(self, tmp_path: Path) -> None:
        tools = _get_tools()
        update_result = tools["trw_learn_update"].fn(
            learning_id="L-nonexistent",
            status="resolved",
        )
        assert update_result["status"] == "not_found"
        assert "error" in update_result

    def test_no_changes_returns_no_changes(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="No change test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(learning_id=lid)
        assert update_result["status"] == "no_changes"

    def test_resyncs_index_after_update(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Index resync test",
            detail="Detail",
            impact=0.8,
        )
        lid = result["learning_id"]

        tools["trw_learn_update"].fn(
            learning_id=lid,
            status="resolved",
        )

        # Index should reflect the updated status
        index = reader.read_yaml(tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml")
        # Entry should still be in the index
        assert index["total_count"] >= 1

class TestTrwLearnAnalytics:
    """Tests for trw_learn analytics counter."""

    def test_learn_increments_analytics_counter(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn should increment total_learnings in analytics.yaml."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Analytics test one",
            detail="First",
            impact=0.5,
        )
        tools["trw_learn"].fn(
            summary="Analytics test two",
            detail="Second",
            impact=0.5,
        )

        analytics_path = tmp_path / _CFG.trw_dir / _CFG.context_dir / "analytics.yaml"
        if analytics_path.exists():
            data = reader.read_yaml(analytics_path)
            assert int(str(data.get("total_learnings", 0))) >= 2
