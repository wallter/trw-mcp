"""Tests for _cleanup.py — FR20 cleanup actions + T0 beacon exemption."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    MarkersConfig,
)
from trw_mcp.channels._cleanup import (
    cleanup_channel,
    is_t0_beacon,
    tombstone_content,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = "<!-- trw-distill:start -->"
END = "<!-- trw-distill:end -->"


def _entry(
    action: CleanupAction = CleanupAction.NONE,
    tier_default: str = "T2",
    markers: MarkersConfig | None = None,
) -> ChannelEntry:
    cleanup = CleanupConfig(trigger=CleanupTrigger.TTL_EXCEEDED, action=action)
    return ChannelEntry(
        id="test-ch",
        client="claude-code",
        surface=ChannelSurface.CLAUDE_MD_SEGMENT,
        telemetry_tag="test-ch",
        tier_default=tier_default,
        cleanup=cleanup,
        markers=markers or MarkersConfig(start=START, end=END),
    )


# ---------------------------------------------------------------------------
# is_t0_beacon
# ---------------------------------------------------------------------------


def test_is_t0_beacon_long_content() -> None:
    long_content = "PROVENANCE\n" + "x" * 300
    assert is_t0_beacon(long_content) is False


def test_is_t0_beacon_short_no_provenance() -> None:
    assert is_t0_beacon("short content") is False


def test_is_t0_beacon_matches() -> None:
    content = "<!-- TRW:PROVENANCE\nchannel_id: test\n-->"
    assert is_t0_beacon(content) is True


def test_is_t0_beacon_empty() -> None:
    assert is_t0_beacon("") is False


# ---------------------------------------------------------------------------
# tombstone_content
# ---------------------------------------------------------------------------


def test_tombstone_content_includes_channel_id() -> None:
    content = tombstone_content(
        channel_id="my-ch",
        regenerate_cmd="trw-mcp channel-render my-ch --force",
        reason="TTL_EXCEEDED",
    )
    assert "my-ch" in content
    assert "trw-mcp channel-render my-ch --force" in content
    assert "TTL_EXCEEDED" in content


def test_tombstone_content_includes_stale_marker() -> None:
    content = tombstone_content(
        channel_id="ch",
        regenerate_cmd="cmd",
        reason="reason",
    )
    assert "TRW DISTILL STALE" in content


# ---------------------------------------------------------------------------
# NONE action
# ---------------------------------------------------------------------------


def test_cleanup_none_returns_noop(tmp_path: Path) -> None:
    entry = _entry(action=CleanupAction.NONE)
    result = cleanup_channel(
        entry=entry,
        target_path=tmp_path / "target.md",
        trigger=CleanupTrigger.NONE,
    )
    assert result["status"] == "noop"


# ---------------------------------------------------------------------------
# SUPPRESS action
# ---------------------------------------------------------------------------


def test_cleanup_suppress_no_output(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("existing content")
    entry = _entry(action=CleanupAction.SUPPRESS)
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "suppressed"
    # File untouched
    assert target.read_text() == "existing content"


# ---------------------------------------------------------------------------
# FULL_PRUNE action
# ---------------------------------------------------------------------------


def test_cleanup_full_prune_deletes_file(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("some content to prune")
    entry = _entry(action=CleanupAction.FULL_PRUNE)
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "pruned"
    assert not target.exists()


def test_cleanup_full_prune_missing_file_ok(tmp_path: Path) -> None:
    target = tmp_path / "nonexistent.md"
    entry = _entry(action=CleanupAction.FULL_PRUNE)
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "pruned"


# ---------------------------------------------------------------------------
# CLEAR_SEGMENT action
# ---------------------------------------------------------------------------


def test_cleanup_clear_segment_empties_interior(tmp_path: Path) -> None:
    content = f"before\n{START}\nsome content\n{END}\nafter"
    target = tmp_path / "target.md"
    target.write_text(content)
    entry = _entry(action=CleanupAction.CLEAR_SEGMENT)
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "segment_cleared"
    written = target.read_text()
    assert START in written
    assert END in written
    # Interior should be empty (just whitespace between markers)
    start_idx = written.find(START) + len(START)
    end_idx = written.find(END)
    interior = written[start_idx:end_idx]
    assert interior.strip() == ""


def test_cleanup_clear_segment_preserves_surrounding_content(tmp_path: Path) -> None:
    prefix = "# Header\n\n"
    suffix = "\n\n## Footer"
    content = f"{prefix}{START}\ninterior\n{END}{suffix}"
    target = tmp_path / "target.md"
    target.write_text(content)
    entry = _entry(action=CleanupAction.CLEAR_SEGMENT)
    cleanup_channel(entry=entry, target_path=target, trigger=CleanupTrigger.NONE)
    written = target.read_text()
    assert prefix in written
    assert suffix in written


# ---------------------------------------------------------------------------
# TOMBSTONE action
# ---------------------------------------------------------------------------


def test_cleanup_tombstone_writes_stale_notice(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    entry = _entry(action=CleanupAction.TOMBSTONE)
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "tombstone_written"
    assert target.exists()
    written = target.read_text()
    assert "TRW DISTILL STALE" in written
    assert "test-ch" in written


def test_cleanup_tombstone_includes_regenerate_command(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    entry = _entry(action=CleanupAction.TOMBSTONE)
    cleanup_channel(entry=entry, target_path=target, trigger=CleanupTrigger.TTL_EXCEEDED)
    written = target.read_text()
    assert "channel-render" in written


# ---------------------------------------------------------------------------
# TIER_DOWN action
# ---------------------------------------------------------------------------


def test_cleanup_tier_down_returns_lower_tier(tmp_path: Path) -> None:
    entry = _entry(action=CleanupAction.TIER_DOWN, tier_default="T3")
    result = cleanup_channel(
        entry=entry,
        target_path=tmp_path / "target.md",
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "tier_down"
    assert result["tier_used"] == "T2"


def test_cleanup_tier_down_to_t0(tmp_path: Path) -> None:
    entry = _entry(action=CleanupAction.TIER_DOWN_TO_T0, tier_default="T3")
    result = cleanup_channel(
        entry=entry,
        target_path=tmp_path / "target.md",
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "tier_down_to_t0"
    assert result["tier_used"] == "T0"


# ---------------------------------------------------------------------------
# T0 beacon exemption — FULL_PRUNE and CLEAR_SEGMENT skip
# ---------------------------------------------------------------------------


def test_t0_beacon_exempt_full_prune(tmp_path: Path) -> None:
    """T0 beacon content → FULL_PRUNE skips (skipped_t0_exempt)."""
    t0_content = "<!-- TRW:PROVENANCE\nchannel_id: test-ch\n-->"
    target = tmp_path / "target.md"
    target.write_text(t0_content)
    entry = _entry(action=CleanupAction.FULL_PRUNE, tier_default="T2")
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "skipped_t0_exempt"
    # File must NOT have been deleted
    assert target.exists()


def test_t0_beacon_exempt_clear_segment(tmp_path: Path) -> None:
    """T0 beacon content → CLEAR_SEGMENT skips."""
    t0_content = "<!-- TRW:PROVENANCE\nchannel_id: test-ch\n-->"
    target = tmp_path / "target.md"
    target.write_text(t0_content)
    entry = _entry(action=CleanupAction.CLEAR_SEGMENT, tier_default="T2")
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "skipped_t0_exempt"
    assert target.read_text() == t0_content


def test_t0_tier_default_exempt_full_prune(tmp_path: Path) -> None:
    """tier_default=T0 → FULL_PRUNE always skips even without beacon content."""
    target = tmp_path / "target.md"
    target.write_text("substantive content here that is not a beacon")
    entry = _entry(action=CleanupAction.FULL_PRUNE, tier_default="T0")
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.TTL_EXCEEDED,
    )
    assert result["status"] == "skipped_t0_exempt"


def test_suppress_still_works_on_t0_channel(tmp_path: Path) -> None:
    """SUPPRESS is the only action that can produce no-content on T0."""
    target = tmp_path / "target.md"
    target.write_text("existing content")
    entry = _entry(action=CleanupAction.SUPPRESS, tier_default="T0")
    result = cleanup_channel(
        entry=entry,
        target_path=target,
        trigger=CleanupTrigger.DISABLED,
    )
    assert result["status"] == "suppressed"
