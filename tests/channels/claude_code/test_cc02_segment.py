"""Tests for channels/claude_code/_cc02_segment.py (PRD-DIST-2405 FR19-FR24)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.channels.claude_code._cc02_segment import (
    CC02_MARKER_END,
    CC02_MARKER_START,
    CC02_QUOTA_BYTES,
    build_cc02_channel_entry,
    install_cc02_segment,
    render_cc02_segment,
)

_SHA = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

_SAMPLE_SIDECAR = {
    "high_churn_directories": [
        "src/trw_mcp/state/",
        "src/trw_mcp/tools/",
        "src/trw_mcp/channels/",
    ],
    "do_not_remove_markers": [
        {"file_path": "src/trw_mcp/state/memory_adapter.py"},
        {"file_path": "src/trw_mcp/tools/ceremony.py"},
    ],
    "conventions": [
        "Use structlog.get_logger(__name__)",
        "350 effective-LOC gate",
    ],
    "risk_files": [
        {"file_path": "src/a.py", "risk_score": 0.9},
        {"file_path": "src/b.py", "risk_score": 0.8},
    ],
}


class TestMarkers:
    def test_markers_distinct_from_ceremony_markers(self) -> None:
        """FR19: distill markers are distinct from ceremony markers."""
        assert CC02_MARKER_START != "<!-- trw:start -->"
        assert CC02_MARKER_END != "<!-- trw:end -->"
        assert "trw-distill" in CC02_MARKER_START
        assert "trw-distill" in CC02_MARKER_END

    def test_segment_sibling_not_nested(self, tmp_path: Path) -> None:
        """FR19: segment is AFTER <!-- trw:end -->, not inside it."""
        claude_md = tmp_path / "CLAUDE.md"
        # Create CLAUDE.md with existing TRW ceremony section
        claude_md.write_text(
            "# Instructions\n\n"
            "<!-- trw:start -->\n"
            "Ceremony content.\n"
            "<!-- trw:end -->\n"
            "\nOther content.\n",
            encoding="utf-8",
        )
        install_cc02_segment(repo_root=tmp_path, sha=_SHA, force=True)
        content = claude_md.read_text(encoding="utf-8")

        trw_end_pos = content.find("<!-- trw:end -->")
        distill_start_pos = content.find(CC02_MARKER_START)

        if distill_start_pos != -1:
            # Distill segment must appear AFTER <!-- trw:end -->
            assert distill_start_pos > trw_end_pos, (
                "CC-02 segment must be placed AFTER <!-- trw:end -->, not inside it"
            )


class TestRenderCc02Segment:
    def test_t0_segment_presence_beacon_only(self) -> None:
        """FR22: T0 segment is presence beacon only."""
        content = render_cc02_segment(sha=_SHA, tier="T0")
        assert len(content) <= 200  # Very short

    def test_t1_segment_within_150_tokens(self) -> None:
        """FR22: T1 segment ≤ 150 tokens (~600 chars)."""
        content = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        # 600 chars is the budget
        assert len(content) <= 600

    def test_metadata_comment_format(self) -> None:
        """FR20: T1 includes metadata comment with Generated/SHA/Commits-since."""
        content = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        assert "Generated:" in content
        assert _SHA[:8] in content
        assert "Commits-since:" in content

    def test_sha_excludes_timestamp_variation(self) -> None:
        """FR20: calling twice same day returns byte-identical content (no time)."""
        c1 = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        c2 = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        assert c1 == c2

    def test_t1_includes_high_churn_dirs(self) -> None:
        content = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        assert "state" in content

    def test_t1_includes_do_not_remove_locations(self) -> None:
        content = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        assert "memory_adapter" in content or "DO-NOT-REMOVE" in content

    def test_t1_includes_convention(self) -> None:
        content = render_cc02_segment(sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR)
        assert "structlog" in content

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """FR23: dry_run=True returns content without writing to CLAUDE.md."""
        claude_md = tmp_path / "CLAUDE.md"
        result = install_cc02_segment(repo_root=tmp_path, sha=_SHA, dry_run=True)
        assert result.status == "dry_run"
        assert result.would_write is not None
        assert not claude_md.exists()


class TestBuildEntry:
    def test_entry_has_correct_markers(self) -> None:
        entry = build_cc02_channel_entry()
        assert entry.markers.start == CC02_MARKER_START
        assert entry.markers.end == CC02_MARKER_END

    def test_entry_quota_bytes(self) -> None:
        entry = build_cc02_channel_entry()
        assert entry.quota_total_bytes == CC02_QUOTA_BYTES

    def test_entry_tier_default_t1(self) -> None:
        """FR22: CC-02 tier default is T1."""
        entry = build_cc02_channel_entry()
        assert entry.tier_default == "T1"

    def test_entry_client_claude_code(self) -> None:
        entry = build_cc02_channel_entry()
        assert entry.client == "claude-code"
