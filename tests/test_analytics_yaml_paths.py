"""Targeted coverage tests for analytics.py — covering YAML fallback paths.

Covers previously uncovered lines:
- 406-431: surface_validated_learnings YAML fallback path
- 459-460: has_existing_success_learning SQLite match path
- 469: has_existing_success_learning YAML match path
- 498-499: has_existing_mechanical_learning SQLite match path
- 727-735: update_analytics_extended YAML fallback for q-learning scan
- 764-765: mark_promoted SQLite exception path
- 1094: auto_prune_excess_entries YAML path early return
- 1108-1110: YAML prune path dedup actions (not dry_run)
- 1116-1125: YAML prune path utility actions + resync
- 1202-1203: compute_reflection_quality SQLite exception fallback
- 1206-1219: compute_reflection_quality YAML fallback scan
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.analytics import (
    auto_prune_excess_entries,
    compute_reflection_quality,
    has_existing_mechanical_learning,
    has_existing_success_learning,
    mark_promoted,
    surface_validated_learnings,
    update_analytics_extended,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trw(tmp_path: Path) -> Path:
    """Create minimal .trw/ structure and return trw_dir."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    return trw_dir


def _write_entry(
    entries_dir: Path,
    entry_id: str,
    *,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    q_value: float = 0.0,
    q_observations: int = 0,
    access_count: int = 0,
    source_type: str = "agent",
    tags: list[str] | None = None,
    last_accessed_at: str | None = None,
) -> Path:
    """Write a YAML entry to disk."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    data: dict[str, object] = {
        "id": entry_id,
        "summary": summary,
        "detail": f"detail for {entry_id}",
        "tags": tags or ["test"],
        "impact": impact,
        "status": status,
        "source_type": source_type,
        "created": last_accessed_at or today,
        "last_accessed_at": last_accessed_at or today,
        "q_value": q_value,
        "q_observations": q_observations,
        "access_count": access_count,
    }
    path = entries_dir / f"{entry_id}.yaml"
    _writer.write_yaml(path, data)
    return path


# ---------------------------------------------------------------------------
# surface_validated_learnings YAML fallback (lines 406-431)
# ---------------------------------------------------------------------------


class TestSurfaceValidatedLearningsYamlFallback:
    """Test YAML fallback in surface_validated_learnings."""

    def test_yaml_fallback_no_entries_dir_returns_empty(self, tmp_path: Path) -> None:
        """Line 412: YAML fallback returns [] when entries_dir does not exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        # Do NOT create entries dir — it should not exist

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )
        assert result == []

    def test_yaml_fallback_returns_validated_entries(self, tmp_path: Path) -> None:
        """Lines 406-431: when SQLite fails, YAML fallback returns validated learnings."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        # Write entries with high q_value and q_observations
        _write_entry(
            entries_dir,
            "validated-1",
            summary="validated learning",
            q_value=0.8,
            q_observations=5,
        )
        _write_entry(
            entries_dir,
            "not-validated",
            summary="low q learning",
            q_value=0.1,
            q_observations=1,
        )
        _write_entry(
            entries_dir,
            "cold-start",
            summary="cold start learning",
            q_value=0.9,
            q_observations=0,
        )
        # Non-active should be skipped
        _write_entry(
            entries_dir,
            "resolved-entry",
            summary="resolved",
            status="resolved",
            q_value=0.9,
            q_observations=5,
        )

        # Force SQLite to fail
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )

        assert len(result) == 1
        assert result[0]["learning_id"] == "validated-1"
        assert result[0]["q_value"] == 0.8
        assert result[0]["q_observations"] == 5

    def test_yaml_fallback_sorted_by_q_value(self, tmp_path: Path) -> None:
        """Lines 406-431: results are sorted by q_value descending."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "low-q", q_value=0.6, q_observations=5)
        _write_entry(entries_dir, "high-q", q_value=0.9, q_observations=5)
        _write_entry(entries_dir, "mid-q", q_value=0.75, q_observations=5)

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )

        assert len(result) == 3
        assert result[0]["learning_id"] == "high-q"
        assert result[1]["learning_id"] == "mid-q"
        assert result[2]["learning_id"] == "low-q"


# ---------------------------------------------------------------------------
# has_existing_success_learning SQLite + YAML match (lines 459-460, 469)
# ---------------------------------------------------------------------------


class TestHasExistingSuccessLearning:
    """Test has_existing_success_learning match branches."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 459-460: SQLite exception falls through to YAML scan."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "success-1",
            summary="Success: reflection complete 3x in session",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True

    def test_sqlite_match_returns_true(self, tmp_path: Path) -> None:
        """Lines 459-460: SQLite path finds a matching summary prefix."""
        trw_dir = _setup_trw(tmp_path)

        mock_list = MagicMock(
            return_value=[
                {"summary": "Success: reflection complete 3x in session"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True

    def test_yaml_match_returns_true(self, tmp_path: Path) -> None:
        """Line 469: YAML fallback finds a matching summary."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "success-1",
            summary="Success: reflection complete 3x in session",
        )

        # SQLite returns no match; YAML fallback finds it
        mock_list = MagicMock(return_value=[])
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True


# ---------------------------------------------------------------------------
# has_existing_mechanical_learning SQLite match (lines 498-499)
# ---------------------------------------------------------------------------


class TestHasExistingMechanicalLearning:
    """Test has_existing_mechanical_learning SQLite match branch."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 498-499: SQLite exception falls through to YAML scan."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "mech-1",
            summary="Repeated operation: file_modified 5x",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: file_modified",
            )
        assert result is True

    def test_sqlite_match_returns_true(self, tmp_path: Path) -> None:
        """Lines 498-499: SQLite path finds a matching prefix."""
        trw_dir = _setup_trw(tmp_path)

        mock_list = MagicMock(
            return_value=[
                {"summary": "Repeated operation: file_modified 5x"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: file_modified",
            )
        assert result is True

    def test_sqlite_no_match_falls_through(self, tmp_path: Path) -> None:
        """SQLite path returns no match, falls through to YAML."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "mech-1",
            summary="Repeated operation: checkpoint 3x",
        )

        # SQLite returns entries but none match the prefix
        mock_list = MagicMock(
            return_value=[
                {"summary": "unrelated learning"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: checkpoint",
            )
        assert result is True


# ---------------------------------------------------------------------------
# update_analytics_extended YAML fallback for q-learning (lines 727-735)
# ---------------------------------------------------------------------------


class TestUpdateAnalyticsExtendedYamlFallback:
    """Test YAML fallback in update_analytics_extended for q-learning scan."""

    def test_yaml_fallback_counts_q_activations_and_high_impact(self, tmp_path: Path) -> None:
        """Lines 727-735: YAML fallback scans entries for q_observations and impact."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        # Entry with q_observations > 0
        _write_entry(entries_dir, "q-active", q_observations=3, impact=0.5)
        # Entry with high impact
        _write_entry(entries_dir, "high-impact", q_observations=0, impact=0.8)
        # Entry with both
        _write_entry(entries_dir, "both", q_observations=2, impact=0.9)

        # Force SQLite to fail
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            update_analytics_extended(
                trw_dir,
                new_learnings_count=1,
                is_reflection=True,
                is_success=True,
            )

        # Read back analytics
        reader = FileStateReader()
        analytics_path = trw_dir / "context" / "analytics.yaml"
        data = reader.read_yaml(analytics_path)

        assert data["q_learning_activations"] == 2  # q-active + both
        assert data["high_impact_learnings"] == 2  # high-impact + both


# ---------------------------------------------------------------------------
# mark_promoted SQLite exception (lines 764-765)
# ---------------------------------------------------------------------------


class TestMarkPromotedSqliteException:
    """Test mark_promoted when SQLite fails."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 764-765: SQLite exception is caught, YAML update still happens."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "promote-me", summary="promote target")

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            side_effect=RuntimeError("sqlite broken"),
        ):
            mark_promoted(trw_dir, "promote-me")

        # Verify YAML was updated
        reader = FileStateReader()
        path = entries_dir / "promote-me.yaml"
        data = reader.read_yaml(path)
        assert data.get("promoted_to_claude_md") is True


# ---------------------------------------------------------------------------
# auto_prune_excess_entries YAML path (lines 1094, 1108-1110, 1116-1125)
# ---------------------------------------------------------------------------


class TestAutoPruneYamlPath:
    """Test auto_prune_excess_entries YAML fallback path."""

    def test_yaml_path_early_return_under_threshold(self, tmp_path: Path) -> None:
        """Line 1094: YAML path returns early when active_count <= max_entries."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "entry-1")
        _write_entry(entries_dir, "entry-2")

        # Force SQLite to fail
        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            result = auto_prune_excess_entries(
                trw_dir,
                max_entries=10,
            )

        assert result["actions_taken"] == 0
        assert result["active_count"] == 2

    def test_yaml_path_dedup_and_utility_prune(self, tmp_path: Path) -> None:
        """Lines 1108-1110, 1116-1125: YAML path performs dedup + utility pruning."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        # Create enough entries to exceed max_entries threshold
        # Make duplicates (similar summaries for Jaccard match)
        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        for i in range(5):
            _write_entry(
                entries_dir,
                f"entry-{i}",
                summary=f"testing coverage gap in module {i % 2}",
                impact=0.1,
                last_accessed_at=old_date,
            )

        # Force SQLite to fail
        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            # Mock find_duplicate_learnings to return a duplicate pair
            with patch(
                "trw_mcp.state.analytics.dedup.find_duplicate_learnings",
                return_value=[("entry-0", "entry-1", 0.85)],
            ):
                # Mock utility_based_prune_candidates to return a candidate
                with patch(
                    "trw_mcp.scoring.utility_based_prune_candidates",
                    return_value=[
                        {"id": "entry-2", "suggested_status": "obsolete"},
                        {"id": "entry-3", "suggested_status": "resolved"},
                        # entry-0 is already in dedup_ids, should be skipped
                        {"id": "entry-0", "suggested_status": "obsolete"},
                        # No suggested_status field or invalid
                        {"id": "entry-4", "suggested_status": "active"},
                    ],
                ):
                    with patch("trw_mcp.state.analytics.dedup.apply_status_update") as mock_apply:
                        with patch("trw_mcp.state.analytics.dedup.resync_learning_index") as mock_resync:
                            result = auto_prune_excess_entries(
                                trw_dir,
                                max_entries=2,
                                dry_run=False,
                            )

        # 1 dedup action + 2 utility actions (entry-2 obsolete, entry-3 resolved)
        assert result["actions_taken"] == 3
        # resync should have been called since actions > 0
        mock_resync.assert_called_once()

    def test_yaml_path_dry_run_no_actions(self, tmp_path: Path) -> None:
        """Lines 1108-1110: dry_run=True does not apply any changes."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        for i in range(5):
            _write_entry(
                entries_dir,
                f"entry-{i}",
                summary=f"testing coverage gap {i}",
                impact=0.1,
                last_accessed_at=old_date,
            )

        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            with patch(
                "trw_mcp.state.analytics.dedup.find_duplicate_learnings",
                return_value=[("entry-0", "entry-1", 0.9)],
            ):
                with patch(
                    "trw_mcp.scoring.utility_based_prune_candidates",
                    return_value=[],
                ):
                    result = auto_prune_excess_entries(
                        trw_dir,
                        max_entries=2,
                        dry_run=True,
                    )

        assert result["actions_taken"] == 0
        # Dedup candidates still reported
        candidates = result["dedup_candidates"]
        assert isinstance(candidates, list)
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# compute_reflection_quality YAML fallback (lines 1202-1203, 1206-1219)
# ---------------------------------------------------------------------------


class TestComputeReflectionQualityYamlFallback:
    """Test compute_reflection_quality YAML fallback path."""

    def test_yaml_fallback_scans_entries(self, tmp_path: Path) -> None:
        """Lines 1202-1203, 1206-1219: SQLite fails, YAML scan counts metrics."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"
        reflections_dir = trw_dir / "reflections"

        # Create a reflection file
        _writer.write_yaml(
            reflections_dir / "ref-001.yaml",
            {"new_learnings": ["L-001", "L-002"]},
        )

        # Create entries with various attributes
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

        # Force SQLite to fail
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

        # Multiple entries with diverse attributes to cover all branches
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
        # Entry with no tags (just empty list — default)
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

        # Verify it ran through the YAML path and produced a valid score
        assert 0.0 <= float(str(result["score"])) <= 1.0
        diagnostics = result.get("diagnostics", {})
        assert isinstance(diagnostics, dict)
