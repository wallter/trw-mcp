"""Tests for instruction_segment/_renderer.py — 11-step render sequence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.channels._lock import ChannelLockSkip
from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelSurface,
    HumanEditDetection,
    MarkersConfig,
)
from trw_mcp.channels.instruction_segment import (
    InstructionSegmentResult,
    render_instruction_segment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    channel_id: str = "test-ch",
    client: str = "codex",
    tier_default: str = "T2",
    file: str = "AGENTS.md",
    markers_start: str = "<!-- trw:codex:start -->",
    markers_end: str = "<!-- trw:codex:end -->",
    human_edit_detection: HumanEditDetection = HumanEditDetection.NONE,
    quota_total_bytes: int | None = None,
    ttl_commits: int | None = None,
) -> ChannelEntry:
    return ChannelEntry(
        id=channel_id,
        client=client,
        surface=ChannelSurface.CODEX_AGENTS_MD_SEGMENT,
        telemetry_tag="test",
        file=file,
        tier_default=tier_default,
        human_edit_detection=human_edit_detection,
        markers=MarkersConfig(start=markers_start, end=markers_end),
        quota_total_bytes=quota_total_bytes,
        ttl_commits=ttl_commits,
    )


def _content_for_tier(tier: str) -> str:
    return f"rendered-content-at-{tier}"


# ---------------------------------------------------------------------------
# Happy path — full 11-step
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_writes_file_and_returns_written(self, tmp_path):
        entry = _make_entry(file="AGENTS.md")
        repo_root = tmp_path

        result = render_instruction_segment(
            entry=entry,
            repo_root=repo_root,
            sidecar_sha="abc12345",
            content_for_tier=_content_for_tier,
        )

        assert result.status == "written"
        assert result.tier_used == "T2"
        assert result.bytes_written is not None
        assert result.bytes_written > 0
        target = repo_root / "AGENTS.md"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "rendered-content-at-T2" in content

    def test_written_file_contains_provenance(self, tmp_path):
        entry = _make_entry()
        render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="deadbeef",
            content_for_tier=_content_for_tier,
        )
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "TRW:PROVENANCE" in content
        assert "deadbeef" in content

    def test_written_file_contains_markers(self, tmp_path):
        entry = _make_entry(
            markers_start="<!-- trw:codex:start -->",
            markers_end="<!-- trw:codex:end -->",
        )
        render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc12345",
            content_for_tier=_content_for_tier,
        )
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "<!-- trw:codex:start -->" in content
        assert "<!-- trw:codex:end -->" in content

    def test_channel_id_in_result(self, tmp_path):
        entry = _make_entry(channel_id="my-channel")
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha=None,
            content_for_tier=_content_for_tier,
        )
        assert result.channel_id == "my-channel"


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_would_write(self, tmp_path):
        entry = _make_entry()
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc",
            content_for_tier=_content_for_tier,
            dry_run=True,
        )
        assert result.status == "dry_run"
        assert result.would_write is not None
        assert "rendered-content-at-T2" in result.would_write

    def test_dry_run_does_not_write_file(self, tmp_path):
        entry = _make_entry(file="AGENTS.md")
        render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc",
            content_for_tier=_content_for_tier,
            dry_run=True,
        )
        assert not (tmp_path / "AGENTS.md").exists()

    def test_dry_run_bytes_written_matches_would_write_length(self, tmp_path):
        entry = _make_entry()
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc",
            content_for_tier=_content_for_tier,
            dry_run=True,
        )
        assert result.would_write is not None
        assert result.bytes_written == len(result.would_write.encode("utf-8"))


# ---------------------------------------------------------------------------
# ChannelLockSkip → skipped_lock
# ---------------------------------------------------------------------------


class TestLockSkip:
    def test_lock_skip_returns_skipped_lock_status(self, tmp_path):
        entry = _make_entry()
        with patch("trw_mcp.channels.instruction_segment._renderer.ChannelLock") as MockLock:
            mock_instance = MagicMock()
            mock_instance.__enter__ = MagicMock(side_effect=ChannelLockSkip(Path("some.lock")))
            MockLock.return_value = mock_instance

            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="abc",
                content_for_tier=_content_for_tier,
            )

        assert result.status == "skipped_lock"
        assert result.channel_id == entry.id


# ---------------------------------------------------------------------------
# Conflict detection → skipped_conflict
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_conflict_detected_returns_skipped_conflict(self, tmp_path):
        """RENDER_LOG mode detects that the full-file SHA changed after human edit."""
        entry = _make_entry(
            human_edit_detection=HumanEditDetection.RENDER_LOG,
        )

        # First write establishes baseline (state includes render SHA)
        render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="sha1",
            content_for_tier=_content_for_tier,
        )

        # The state file records segment_interior_sha256; for RENDER_LOG mode
        # we need to patch detect_human_edit to return True to simulate a conflict.
        # We verify the code path by patching at the source.
        target = tmp_path / "AGENTS.md"
        with patch(
            "trw_mcp.channels.instruction_segment._renderer.detect_human_edit",
            return_value=True,
        ):
            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="sha2",
                content_for_tier=_content_for_tier,
            )

        assert result.status == "skipped_conflict"
        assert result.conflict_detected is True

    def test_force_bypasses_conflict(self, tmp_path):
        """force=True skips detect_human_edit even when it would return True."""
        entry = _make_entry(
            human_edit_detection=HumanEditDetection.RENDER_LOG,
        )

        # Force=True bypasses conflict detection even if detect_human_edit=True
        with patch(
            "trw_mcp.channels.instruction_segment._renderer.detect_human_edit",
            return_value=True,
        ):
            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="sha2",
                content_for_tier=_content_for_tier,
                force=True,
            )

        assert result.status == "written"


# ---------------------------------------------------------------------------
# Quota enforcement — tier-down
# ---------------------------------------------------------------------------


class TestQuotaEnforcement:
    def test_quota_exceeded_triggers_tier_down(self, tmp_path):
        """When T2 content exceeds quota, render should tier-down to T1 or T0."""

        def content_at_tier(tier: str) -> str:
            if tier == "T2":
                # Large content that exceeds the 20-byte quota
                return "x" * 100
            return "short"  # fits

        entry = _make_entry(
            tier_default="T2",
            quota_total_bytes=20,
        )
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc",
            content_for_tier=content_at_tier,
        )
        # Should have tiered down (not T2)
        assert result.status == "written"
        assert result.tier_used != "T2"

    def test_within_quota_no_tier_down(self, tmp_path):
        entry = _make_entry(tier_default="T2", quota_total_bytes=10_000)
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="abc",
            content_for_tier=_content_for_tier,
        )
        assert result.status == "written"
        assert result.tier_used == "T2"


# ---------------------------------------------------------------------------
# Telemetry emitted
# ---------------------------------------------------------------------------


class TestTelemetryEmitted:
    def test_telemetry_event_emitted_on_write(self, tmp_path):
        entry = _make_entry()
        with patch("trw_mcp.channels.instruction_segment._renderer.append_channel_event") as mock_emit:
            render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="abc",
                content_for_tier=_content_for_tier,
            )
        mock_emit.assert_called()
        # At least one call with event_type="push_write"
        push_calls = [c for c in mock_emit.call_args_list if c.kwargs.get("event_type") == "push_write"]
        assert len(push_calls) >= 1

    def test_telemetry_emitted_on_dry_run(self, tmp_path):
        entry = _make_entry()
        with patch("trw_mcp.channels.instruction_segment._renderer.append_channel_event") as mock_emit:
            render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="abc",
                content_for_tier=_content_for_tier,
                dry_run=True,
            )
        mock_emit.assert_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_missing_entry_file_returns_error(self, tmp_path):
        entry = ChannelEntry(
            id="no-file",
            client="codex",
            surface=ChannelSurface.CODEX_AGENTS_MD_SEGMENT,
            telemetry_tag="test",
            file=None,  # not set
        )
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha=None,
            content_for_tier=_content_for_tier,
        )
        assert result.status == "error"
        assert result.error is not None

    def test_result_is_instruction_segment_result_instance(self, tmp_path):
        entry = _make_entry()
        result = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha=None,
            content_for_tier=_content_for_tier,
        )
        assert isinstance(result, InstructionSegmentResult)


# ---------------------------------------------------------------------------
# HIGH-1 behavioral tests: distinct event types per code path
# ---------------------------------------------------------------------------


class TestDistinctEventTypes:
    """HIGH-1 fix: each renderer code path emits a distinct event type.

    - Lock contention → channel_lock_skip (NOT channel_conflict)
    - Human edit detected → channel_conflict
    - Internal error → channel_error (NOT channel_conflict)
    """

    def test_lock_skip_emits_channel_lock_skip_not_channel_conflict(self, tmp_path):
        entry = _make_entry()
        with patch("trw_mcp.channels.instruction_segment._renderer.ChannelLock") as MockLock:
            mock_instance = MagicMock()
            mock_instance.__enter__ = MagicMock(side_effect=ChannelLockSkip(Path("some.lock")))
            MockLock.return_value = mock_instance
            with patch("trw_mcp.channels.instruction_segment._renderer.append_channel_event") as mock_emit:
                result = render_instruction_segment(
                    entry=entry,
                    repo_root=tmp_path,
                    sidecar_sha="abc",
                    content_for_tier=_content_for_tier,
                )

        assert result.status == "skipped_lock"
        emitted_types = [c.kwargs.get("event_type") for c in mock_emit.call_args_list]
        assert "channel_lock_skip" in emitted_types, (
            f"Expected channel_lock_skip in emitted events, got {emitted_types}"
        )
        assert "channel_conflict" not in emitted_types, (
            f"channel_conflict must NOT be emitted for lock-skip, got {emitted_types}"
        )

    def test_human_edit_detected_emits_channel_conflict(self, tmp_path):
        entry = _make_entry(human_edit_detection=HumanEditDetection.RENDER_LOG)
        with (
            patch(
                "trw_mcp.channels.instruction_segment._renderer.detect_human_edit",
                return_value=True,
            ),
            patch("trw_mcp.channels.instruction_segment._renderer.append_channel_event") as mock_emit,
        ):
            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="abc",
                content_for_tier=_content_for_tier,
            )

        assert result.status == "skipped_conflict"
        emitted_types = [c.kwargs.get("event_type") for c in mock_emit.call_args_list]
        assert "channel_conflict" in emitted_types, f"Expected channel_conflict for human-edit, got {emitted_types}"

    def test_internal_error_emits_channel_error_not_channel_conflict(self, tmp_path):
        entry = _make_entry()
        with (
            patch(
                "trw_mcp.channels.instruction_segment._renderer._render_under_lock",
                side_effect=RuntimeError("simulated internal error"),
            ),
            patch("trw_mcp.channels.instruction_segment._renderer.append_channel_event") as mock_emit,
        ):
            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="abc",
                content_for_tier=_content_for_tier,
            )

        assert result.status == "error"
        emitted_types = [c.kwargs.get("event_type") for c in mock_emit.call_args_list]
        assert "channel_error" in emitted_types, f"Expected channel_error for internal error, got {emitted_types}"
        assert "channel_conflict" not in emitted_types, (
            f"channel_conflict must NOT be emitted for internal error, got {emitted_types}"
        )


# ---------------------------------------------------------------------------
# HIGH-3 behavioral test: reconcile called before conflict detection
# ---------------------------------------------------------------------------


class TestReconcileCrashRecovery:
    """HIGH-3 fix: reconcile() is called in _render_under_lock before
    detect_human_edit so crash-recovery log resets happen automatically.

    The fix wires reconcile() into the render path (it was previously only
    tested in isolation via test_conflict_detection.py but never called from
    the renderer).
    """

    def test_reconcile_is_called_during_render(self, tmp_path):
        """Verify reconcile() is actually called in the render path (HIGH-3).

        The root bug: reconcile() existed and was tested standalone but was
        NEVER called from _render_under_lock.  This test confirms the wiring.
        """
        entry = _make_entry(human_edit_detection=HumanEditDetection.NONE)

        with patch("trw_mcp.channels.instruction_segment._renderer.reconcile") as mock_reconcile:
            result = render_instruction_segment(
                entry=entry,
                repo_root=tmp_path,
                sidecar_sha="sha1",
                content_for_tier=_content_for_tier,
            )

        assert result.status == "written"
        (
            mock_reconcile.assert_called_once(),
            (
                "reconcile() must be called in _render_under_lock before conflict detection. "
                "HIGH-3 fix: this was previously dead code — never called from the renderer."
            ),
        )

    def test_reconcile_crash_state_clears_phantom_render_log_entry(self, tmp_path):
        """Behavioral test: after a crash+phantom log entry, reconcile() corrects
        the render log so subsequent state is consistent.

        Crash scenario:
        - render log appended with phantom SHA (step 2 of write_atomic)
        - os.rename never happened (crash at step 3/4)
        - file on disk has the old content

        After reconcile(), the log has a reconcile-marker entry with the actual
        SHA, so the log is no longer in the phantom-future-state.
        """
        import json as json_mod

        from trw_mcp.channels._conflict import RenderLog, RenderLogEntry

        entry = _make_entry(human_edit_detection=HumanEditDetection.NONE)
        channels_dir = tmp_path / ".trw" / "channels"
        target = tmp_path / "AGENTS.md"

        # First write establishes a real file on disk
        result1 = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="sha1",
            content_for_tier=_content_for_tier,
        )
        assert result1.status == "written"

        # Append a phantom render-log entry (simulating crash between step 2 and 4)
        render_log = RenderLog(channels_dir / "render-log.jsonl")
        phantom_sha = "b" * 64  # SHA that does NOT match the real file
        phantom_entry = RenderLogEntry(
            channel_id=entry.id,
            target_path=target,
            sha=phantom_sha,
            ts="2026-05-29T00:00:00.000Z",
            bytes_written=999,
        )
        render_log.append(phantom_entry)

        # Verify phantom entry is the latest
        latest = render_log.last_for(entry.id, target)
        assert latest is not None
        assert latest.sha == phantom_sha

        # Now render again — reconcile() is called inside _render_under_lock
        result2 = render_instruction_segment(
            entry=entry,
            repo_root=tmp_path,
            sidecar_sha="sha2",
            content_for_tier=_content_for_tier,
        )
        assert result2.status == "written"

        # After the render, the log should no longer have the phantom SHA as latest.
        # reconcile() should have added a reconcile-marker with the actual file SHA.
        render_log_path = channels_dir / "render-log.jsonl"
        lines = [l for l in render_log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        all_entries = [json_mod.loads(l) for l in lines]
        # There should be at least one entry with event="reconcile"
        reconcile_entries = [e for e in all_entries if e.get("event") == "reconcile"]
        assert reconcile_entries, (
            "Expected a reconcile entry in the render log after crash-state recovery. "
            f"All events: {[e.get('event', 'none') for e in all_entries]}"
        )
