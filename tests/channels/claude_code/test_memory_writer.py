"""Tests for channels/claude_code/_memory_writer.py (PRD-DIST-2405 FR11-FR17)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.channels.claude_code._memory_path import (
    resolve_memory_dir,
)
from trw_mcp.channels.claude_code._memory_writer import (
    MEMORY_INDEX_MARKER_END,
    MEMORY_INDEX_MARKER_START,
    WriteSnapshotResult,
    _is_t0_beacon,
    _load_sidecar,
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
        assert r1.snapshot_path is not None
        content1 = r1.snapshot_path.read_text(encoding="utf-8")
        r2 = write_distill_snapshot(
            repo_root=repo, sha=_SHA, tier="T0", force=True, claude_projects_dir=claude_dir
        )
        assert r2.snapshot_path is not None
        content2 = r2.snapshot_path.read_text(encoding="utf-8")
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

    def test_near_cap_telemetry_exception_swallowed(self, tmp_path: Path) -> None:
        """Covers lines 165-166: telemetry exception in near-cap path is swallowed."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # Pre-populate 195 lines to trigger near-cap
        existing_lines = "\n".join([f"- entry {i}" for i in range(195)])
        (memory_dir / "MEMORY.md").write_text(existing_lines + "\n", encoding="utf-8")
        # Patch append_channel_event to raise — should be swallowed
        with patch(
            "trw_mcp.channels.claude_code._memory_writer.append_channel_event",
            side_effect=RuntimeError("telemetry down"),
        ):
            update_memory_index(memory_dir)
        content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "distill_snapshot.md" in content


class TestLoadSidecar:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = _load_sidecar(tmp_path / "nonexistent.json")
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        result = _load_sidecar(f)
        assert result is None

    def test_returns_none_when_json_not_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "list.json"
        f.write_text("[1, 2, 3]", encoding="utf-8")
        result = _load_sidecar(f)
        assert result is None

    def test_returns_dict_on_valid_sidecar(self, tmp_path: Path) -> None:
        f = tmp_path / "good.json"
        f.write_text('{"schema_version": "risk-report-sidecar/v0", "key": "val"}', encoding="utf-8")
        result = _load_sidecar(f)
        assert result is not None
        assert result["key"] == "val"


class TestIsT0Beacon:
    def test_false_when_file_missing(self, tmp_path: Path) -> None:
        assert _is_t0_beacon(tmp_path / "none.md") is False

    def test_true_when_tier_t0_in_content(self, tmp_path: Path) -> None:
        f = tmp_path / "snap.md"
        f.write_text("---\n# Tier: T0\n---\nTRW distill snapshot present.\n", encoding="utf-8")
        assert _is_t0_beacon(f) is True

    def test_false_when_tier_t2_in_content(self, tmp_path: Path) -> None:
        f = tmp_path / "snap.md"
        f.write_text("---\n# Tier: T2\n---\n## Top Risk Files\n", encoding="utf-8")
        assert _is_t0_beacon(f) is False

    def test_false_on_read_error(self, tmp_path: Path) -> None:
        f = tmp_path / "snap.md"
        f.write_text("Tier: T0", encoding="utf-8")
        # Simulate OSError by removing read permission
        f.chmod(0o000)
        try:
            result = _is_t0_beacon(f)
            assert result is False
        finally:
            f.chmod(0o644)


class TestWriteSnapshotResultAsDict:
    def test_as_dict_returns_all_fields(self) -> None:
        r = WriteSnapshotResult(status="written", bytes_written=100, tier_used="T2")
        d = r.as_dict()
        assert d["status"] == "written"
        assert d["bytes_written"] == 100
        assert d["tier_used"] == "T2"
        assert d["snapshot_path"] is None

    def test_as_dict_with_path(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.md"
        r = WriteSnapshotResult(status="written", snapshot_path=p)
        d = r.as_dict()
        assert str(p) in d["snapshot_path"]


class TestWriteDistillSnapshotCoveragePaths:
    """Cover uncovered paths in write_distill_snapshot."""

    def test_with_explicit_sidecar_path(self, tmp_path: Path) -> None:
        """FR11: sidecar_path override is loaded when provided."""
        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"
        # Write a valid sidecar at an explicit path
        sidecar_file = tmp_path / "my_sidecar.json"
        sidecar_file.write_text(
            '{"schema_version": "risk-report-sidecar/v0", "risk_files": []}',
            encoding="utf-8",
        )
        result = write_distill_snapshot(
            repo_root=repo,
            sha="a" * 40,
            tier="T1",
            sidecar_path=sidecar_file,
            claude_projects_dir=claude_dir,
        )
        assert result.status == "written"

    def test_error_result_on_write_failure(self, tmp_path: Path) -> None:
        """Covers the except Exception block in write_distill_snapshot."""
        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"
        # Patch render_snapshot to raise so we exercise the error path
        with patch(
            "trw_mcp.channels.claude_code._memory_writer.render_snapshot",
            side_effect=RuntimeError("render failed"),
        ):
            result = write_distill_snapshot(
                repo_root=repo,
                sha="a" * 40,
                tier="T0",
                claude_projects_dir=claude_dir,
            )
        assert result.status == "error"

    def test_lock_skip_returns_skipped_lock(self, tmp_path: Path) -> None:
        """Covers the ChannelLockSkip branch in write_distill_snapshot."""
        from trw_mcp.channels._lock import ChannelLockSkip

        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"
        with patch(
            "trw_mcp.channels.claude_code._memory_writer.ChannelLock",
            side_effect=ChannelLockSkip("locked"),
        ):
            result = write_distill_snapshot(
                repo_root=repo,
                sha="a" * 40,
                tier="T0",
                claude_projects_dir=claude_dir,
            )
        assert result.status == "skipped_lock"

    def test_quota_tierdown_t1_when_t2_oversized(self, tmp_path: Path) -> None:
        """Covers quota tier-down logic (lines 236, 243): T2 oversized → tier down to T1."""
        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"

        call_count = {"n": 0}

        def _fake_render(**kwargs: object) -> str:
            call_count["n"] += 1
            tier = kwargs.get("tier", "T2")
            # First call (T2): return oversized content to trigger tier-down
            if call_count["n"] == 1:
                return "x" * 9000  # > SNAPSHOT_QUOTA_BYTES (8192)
            # Second call (T1): return oversized too to trigger T0 fallback
            if call_count["n"] == 2:
                return "y" * 9000
            # Third call (T0): return normal content
            return "---\n# Tier: T0\n---\nTRW distill snapshot present.\n"

        with patch(
            "trw_mcp.channels.claude_code._memory_writer.render_snapshot",
            side_effect=_fake_render,
        ):
            result = write_distill_snapshot(
                repo_root=repo,
                sha="a" * 40,
                tier="T2",
                claude_projects_dir=claude_dir,
            )
        # Should succeed via T0 fallback
        assert result.status == "written"
        assert call_count["n"] == 3  # T2, T1, T0 called

    def test_telemetry_exception_is_swallowed(self, tmp_path: Path) -> None:
        """Covers lines 266-267: telemetry append failure is swallowed (fail-open)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"
        with patch(
            "trw_mcp.channels.claude_code._memory_writer.append_channel_event",
            side_effect=RuntimeError("telemetry error"),
        ):
            result = write_distill_snapshot(
                repo_root=repo,
                sha="a" * 40,
                tier="T0",
                claude_projects_dir=claude_dir,
            )
        # Should still succeed despite telemetry failure
        assert result.status == "written"

    def test_finally_lock_exit_error_swallowed(self, tmp_path: Path) -> None:
        """Covers lines 290-291: __exit__ error in finally is swallowed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        claude_dir = tmp_path / "claude"

        from unittest.mock import MagicMock

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=mock_lock)
        mock_lock.__exit__ = MagicMock(side_effect=RuntimeError("exit error"))

        with patch(
            "trw_mcp.channels.claude_code._memory_writer.ChannelLock",
            return_value=mock_lock,
        ):
            # Should not raise even if __exit__ fails
            result = write_distill_snapshot(
                repo_root=repo,
                sha="a" * 40,
                tier="T0",
                claude_projects_dir=claude_dir,
            )
        assert result.status in ("written", "error")
