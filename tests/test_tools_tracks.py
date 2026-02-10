"""Tests for trw_tracks tool — PRD-CORE-003 concurrent sprint track support.

Covers: track creation/update, listing, status, file conflict detection,
merge ordering, and full lifecycle integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP

from trw_mcp.tools.tracks import register_track_tools


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to a temp directory and create .trw/tracks/."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".trw" / "tracks").mkdir(parents=True, exist_ok=True)
    # Also patch module-level config in tracks.py
    import trw_mcp.tools.tracks as tracks_mod
    from trw_mcp.models.config import TRWConfig
    monkeypatch.setattr(tracks_mod, "_config", TRWConfig())
    return tmp_path


def _get_tools() -> dict[str, object]:
    """Create a fresh server and register track tools, return tool map."""
    srv = FastMCP("test-tracks")
    register_track_tools(srv)
    tools = {t.name: t for t in srv._tool_manager._tools.values()}
    return tools


# ---------------------------------------------------------------------------
# Track Creation and Listing
# ---------------------------------------------------------------------------


class TestTrackCreate:
    """FR02: trw_tracks create action."""

    async def test_create_track_persists(self, tmp_path: Path) -> None:
        """New track is written to registry file."""
        tools = _get_tools()
        result = await tools["trw_tracks"].fn(
            action="create",
            track="A",
            sprint="sprint-6",
            prd_scope=["PRD-CORE-012", "PRD-CORE-003"],
            files=["tools/wave.py", "tools/tracks.py"],
        )
        assert result["action_taken"] == "created"
        assert result["track"] == "A"
        assert result["file_count"] == 2

        # Verify file exists on disk
        registry_path = tmp_path / ".trw" / "tracks" / "sprint-6.yaml"
        assert registry_path.exists()

    async def test_create_track_updates_existing(self, tmp_path: Path) -> None:
        """Same name updates fields, no duplicate."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create",
            track="A",
            sprint="sprint-6",
            files=["tools/wave.py"],
        )
        result = await tools["trw_tracks"].fn(
            action="create",
            track="A",
            sprint="sprint-6",
            files=["tools/wave.py", "tools/tracks.py"],
            prd_scope=["PRD-CORE-003"],
        )
        assert result["action_taken"] == "updated"
        assert result["file_count"] == 2

        # Verify only one track in registry
        list_result = await tools["trw_tracks"].fn(
            action="list",
            sprint="sprint-6",
        )
        assert list_result["track_count"] == 1

    async def test_create_requires_track_name(self) -> None:
        """Create without track name raises ValidationError."""
        tools = _get_tools()
        with pytest.raises(Exception, match="track parameter is required"):
            await tools["trw_tracks"].fn(
                action="create",
                sprint="sprint-6",
            )

    async def test_list_tracks_returns_all(self) -> None:
        """Multiple tracks listed correctly."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            prd_scope=["PRD-CORE-012"], files=["tools/wave.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            prd_scope=["PRD-CORE-003"], files=["tools/tracks.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="C", sprint="sprint-6",
            prd_scope=["PRD-QUAL-001"], files=["models/config.py"],
        )

        result = await tools["trw_tracks"].fn(action="list", sprint="sprint-6")
        assert result["track_count"] == 3
        names = [t["name"] for t in result["tracks"]]
        assert sorted(names) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# File Isolation Tracking
# ---------------------------------------------------------------------------


class TestFileTracking:
    """FR01/FR02: file list stored and updated on tracks."""

    async def test_track_files_recorded(self) -> None:
        """File list stored in track entry."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py", "tools/wave.py"],
        )
        result = await tools["trw_tracks"].fn(
            action="status", track="A", sprint="sprint-6",
        )
        assert result["track"]["files"] == ["server.py", "tools/wave.py"]

    async def test_track_files_updated_on_create(self) -> None:
        """Re-create updates file list."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py", "tools/wave.py", "models/track.py"],
        )
        result = await tools["trw_tracks"].fn(
            action="status", track="A", sprint="sprint-6",
        )
        assert len(result["track"]["files"]) == 3

    async def test_empty_files_allowed(self) -> None:
        """Track with no files is valid."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
        )
        result = await tools["trw_tracks"].fn(
            action="status", track="A", sprint="sprint-6",
        )
        assert result["track"]["files"] == []


# ---------------------------------------------------------------------------
# Conflict Detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """FR05: merge-check detects file-level conflicts."""

    async def test_merge_check_detects_overlap(self) -> None:
        """Overlapping files reported as conflicts."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py", "tools/wave.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            files=["server.py", "tools/tracks.py"],
        )

        result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        assert result["conflict_count"] == 1
        assert result["conflicts"][0]["file_path"] == "server.py"
        assert sorted(result["conflicts"][0]["tracks"]) == ["A", "B"]

    async def test_merge_check_no_overlap(self) -> None:
        """Clean tracks report no conflicts."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["tools/wave.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            files=["tools/tracks.py"],
        )

        result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        assert result["conflict_count"] == 0
        assert result["conflicts"] == []

    async def test_merge_check_severity_classification(self) -> None:
        """server.py = low, models/*.py = high, other = medium."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py", "models/track.py", "tools/orchestration.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            files=["server.py", "models/track.py", "tools/orchestration.py"],
        )

        result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        conflicts_by_file = {c["file_path"]: c for c in result["conflicts"]}

        assert conflicts_by_file["server.py"]["severity"] == "low"
        assert conflicts_by_file["models/track.py"]["severity"] == "high"
        assert conflicts_by_file["tools/orchestration.py"]["severity"] == "medium"


# ---------------------------------------------------------------------------
# Merge Ordering
# ---------------------------------------------------------------------------


class TestMergeOrdering:
    """FR05: merge ordering recommendation."""

    async def test_merge_order_no_conflicts_first(self) -> None:
        """Conflict-free tracks ordered first."""
        tools = _get_tools()
        # Track A overlaps with B on server.py
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py", "tools/wave.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            files=["server.py", "tools/tracks.py"],
        )
        # Track C has no overlaps
        await tools["trw_tracks"].fn(
            action="create", track="C", sprint="sprint-6",
            files=["models/config.py"],
        )

        result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        merge_order = result["merge_order"]
        # C should be first (no conflicts)
        assert merge_order[0]["track_name"] == "C"
        assert merge_order[0]["conflict_count"] == 0

    async def test_merge_order_deterministic(self) -> None:
        """Same input produces same ordering."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            files=["server.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["server.py"],
        )

        result1 = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        result2 = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        order1 = [m["track_name"] for m in result1["merge_order"]]
        order2 = [m["track_name"] for m in result2["merge_order"]]
        assert order1 == order2


# ---------------------------------------------------------------------------
# Status Reporting
# ---------------------------------------------------------------------------


class TestStatusReporting:
    """FR04: trw_tracks status action."""

    async def test_status_returns_track_details(self) -> None:
        """Full track details returned."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            prd_scope=["PRD-CORE-012"], files=["tools/wave.py"],
            run_path="/some/run/path",
        )
        result = await tools["trw_tracks"].fn(
            action="status", track="A", sprint="sprint-6",
        )
        track_data = result["track"]
        assert track_data["name"] == "A"
        assert track_data["sprint"] == "sprint-6"
        assert track_data["prd_scope"] == ["PRD-CORE-012"]
        assert track_data["files"] == ["tools/wave.py"]
        assert track_data["run_path"] == "/some/run/path"
        assert track_data["status"] == "active"

    async def test_status_unknown_track_raises(self) -> None:
        """Unknown track name raises ValidationError."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
        )
        with pytest.raises(Exception, match="not found"):
            await tools["trw_tracks"].fn(
                action="status", track="Z", sprint="sprint-6",
            )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Input validation for trw_tracks."""

    async def test_invalid_action_raises(self) -> None:
        """Invalid action string raises ValidationError."""
        tools = _get_tools()
        with pytest.raises(Exception, match="Invalid action"):
            await tools["trw_tracks"].fn(action="delete")

    async def test_merge_check_empty_sprint(self) -> None:
        """Merge-check on sprint with no tracks returns empty result."""
        tools = _get_tools()
        result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-99",
        )
        assert result["conflicts"] == []
        assert result["merge_order"] == []


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestTrackLifecycleIntegration:
    """Full track lifecycle: create → list → merge-check → verify ordering."""

    async def test_full_lifecycle(self) -> None:
        """Create 3 tracks → list → merge-check → verify ordering."""
        tools = _get_tools()

        # Create 3 tracks with intentional overlaps
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            prd_scope=["PRD-CORE-012", "PRD-CORE-003"],
            files=["tools/wave.py", "tools/tracks.py", "server.py", "models/track.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="B", sprint="sprint-6",
            prd_scope=["PRD-QUAL-001"],
            files=["server.py", "models/config.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="C", sprint="sprint-6",
            prd_scope=["PRD-CORE-014"],
            files=["state/persistence.py"],
        )

        # List all
        list_result = await tools["trw_tracks"].fn(
            action="list", sprint="sprint-6",
        )
        assert list_result["track_count"] == 3

        # Merge check
        merge_result = await tools["trw_tracks"].fn(
            action="merge-check", sprint="sprint-6",
        )
        # server.py overlap (A, B)
        assert merge_result["conflict_count"] >= 1

        # C should merge first (no conflicts)
        first_merge = merge_result["merge_order"][0]
        assert first_merge["track_name"] == "C"
        assert first_merge["conflict_count"] == 0

        # Verify all 3 tracks present in merge order
        merge_names = {m["track_name"] for m in merge_result["merge_order"]}
        assert merge_names == {"A", "B", "C"}

    async def test_cross_sprint_list(self) -> None:
        """List without sprint filter returns tracks across sprints."""
        tools = _get_tools()
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-5",
            files=["old_file.py"],
        )
        await tools["trw_tracks"].fn(
            action="create", track="A", sprint="sprint-6",
            files=["new_file.py"],
        )

        result = await tools["trw_tracks"].fn(action="list")
        assert result["track_count"] == 2
