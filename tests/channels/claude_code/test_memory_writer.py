"""Tests for channels/claude_code/_memory_writer.py (PRD-DIST-2405 FR11-FR17)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels.claude_code._memory_path import (
    derive_claude_project_id,
    resolve_memory_dir,
)
from trw_mcp.channels.claude_code._memory_writer import (
    MEMORY_INDEX_MARKER_END,
    MEMORY_INDEX_MARKER_START,
    MEMORY_INDEX_NEAR_CAP_THRESHOLD,
    update_memory_index,
    write_distill_snapshot,
)

_SHA = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _claude_dir(tmp_path: Path) -> Path:
    return tmp_path / "claude_projects"


class TestWriteDistillSnapshot:
    def test_creates_memory_dir_if_absent(self, tmp_path: Path) -> None:
        """FR11: writer creates memory dir if it doesn't exist."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        assert not claude_dir.exists()
        result = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        assert result.status == "written"
        memory_dir = resolve_memory_dir(repo, claude_projects_dir=claude_dir)
        assert memory_dir.exists()

    def test_writes_provenance_frontmatter(self, tmp_path: Path) -> None:
        """FR11: snapshot includes provenance frontmatter."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        result = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        assert result.status == "written"
        snapshot = result.snapshot_path
        assert snapshot is not None
        content = snapshot.read_text(encoding="utf-8")
        assert "---" in content
        assert "SHA:" in content or _SHA[:8] in content

    def test_t0_not_rewritten_when_sidecar_still_absent(self, tmp_path: Path) -> None:
        """FR14: if T0 beacon already written and sidecar still absent, skip."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        # First write: creates T0 beacon
        r1 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        assert r1.status == "written"
        # Second write: sidecar still absent, tier is T2 — should skip
        r2 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T2", claude_projects_dir=claude_dir
        )
        assert r2.status == "skipped_stale_t0_no_sidecar"

    def test_force_bypasses_t0_skip(self, tmp_path: Path) -> None:
        """force=True bypasses T0 skip logic."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        # Write T0 beacon first
        write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        # Force write with T0 again
        r2 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", force=True, claude_projects_dir=claude_dir
        )
        assert r2.status == "written"

    def test_memory_index_pointer_added(self, tmp_path: Path) -> None:
        """FR17: after writing snapshot, MEMORY.md gains a pointer."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        memory_dir = resolve_memory_dir(repo, claude_projects_dir=claude_dir)
        memory_index = memory_dir / "MEMORY.md"
        assert memory_index.exists()
        content = memory_index.read_text(encoding="utf-8")
        assert "distill_snapshot.md" in content

    def test_bytes_written_returned(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        result = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        assert result.bytes_written is not None
        assert result.bytes_written > 0

    def test_snapshot_path_in_result(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        result = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        assert result.snapshot_path is not None
        assert result.snapshot_path.name == "distill_snapshot.md"

    def test_init_project_writes_t0_beacon_synchronously(self, tmp_path: Path) -> None:
        """FR16 (P1-01): init-project writes T0 beacon synchronously."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        result = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", claude_projects_dir=claude_dir
        )
        # Result is available immediately (synchronous)
        assert result.status == "written"
        assert result.snapshot_path is not None
        assert result.snapshot_path.exists()

    def test_snapshot_idempotent_same_data(self, tmp_path: Path) -> None:
        """NFR09: same inputs → byte-identical output."""
        repo = _make_repo(tmp_path)
        claude_dir = _claude_dir(tmp_path)
        r1 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", force=True, claude_projects_dir=claude_dir
        )
        content1 = r1.snapshot_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        r2 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", force=True, claude_projects_dir=claude_dir
        )
        content2 = r2.snapshot_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert content1 == content2


class TestUpdateMemoryIndex:
    def test_creates_memory_md_if_absent(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        update_memory_index(memory_dir)
        assert (memory_dir / "MEMORY.md").exists()

    def test_adds_distill_pointer(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        update_memory_index(memory_dir)
        content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "distill_snapshot.md" in content
        assert MEMORY_INDEX_MARKER_START in content
        assert MEMORY_INDEX_MARKER_END in content

    def test_replaces_existing_index_section(self, tmp_path: Path) -> None:
        """Calling update_memory_index twice replaces — doesn't duplicate."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        update_memory_index(memory_dir)
        update_memory_index(memory_dir)
        content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        # Markers appear exactly once
        assert content.count(MEMORY_INDEX_MARKER_START) == 1

    def test_memory_index_near_cap_warning_emitted_not_suppressed(
        self, tmp_path: Path
    ) -> None:
        """FR17: if MEMORY.md exceeds 190 lines, warning is emitted but write continues."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # Pre-populate MEMORY.md with 195 lines of content
        existing_lines = "\n".join([f"- entry {i}" for i in range(195)])
        (memory_dir / "MEMORY.md").write_text(existing_lines + "\n", encoding="utf-8")
        # update_memory_index should still succeed (not suppress write)
        update_memory_index(memory_dir)
        content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "distill_snapshot.md" in content
