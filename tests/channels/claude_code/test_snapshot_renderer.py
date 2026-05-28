"""Tests for channels/claude_code/_snapshot_renderer.py (PRD-DIST-2405 FR12-FR13)."""

from __future__ import annotations

from trw_mcp.channels.claude_code._snapshot_renderer import (
    SNAPSHOT_QUOTA_BYTES,
    SNAPSHOT_T0_BODY_MAX_CHARS,
    SNAPSHOT_T1_MAX_CHARS,
    render_snapshot,
)

_SAMPLE_SIDECAR = {
    "risk_files": [
        {"file_path": "src/module_a.py", "risk_score": 0.92, "caution": "DO-NOT-REMOVE markers"},
        {"file_path": "src/module_b.py", "risk_score": 0.75, "caution": "High churn"},
        {"file_path": "src/module_c.py", "risk_score": 0.60, "caution": ""},
        {"file_path": "src/module_d.py", "risk_score": 0.50, "caution": ""},
        {"file_path": "src/module_e.py", "risk_score": 0.40, "caution": ""},
    ],
    "conventions": [
        "Use structlog.get_logger(__name__), never event= kwarg",
        "All public APIs must be fully typed",
        "350 effective-LOC gate per module",
    ],
    "caution_directories": [
        "src/trw_mcp/state/",
        "src/trw_mcp/tools/",
        "src/trw_mcp/channels/",
    ],
}

_SHA = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
_CHANNEL_ID = "cc-01-memory-distill-snapshot"


class TestT0Render:
    def test_t0_has_frontmatter(self) -> None:
        content = render_snapshot(channel_id=_CHANNEL_ID, sha=_SHA, tier="T0")
        assert content.startswith("---")

    def test_t0_presence_beacon_only(self) -> None:
        """T0 body must be ≤ 80 chars excluding frontmatter (FR13)."""
        content = render_snapshot(channel_id=_CHANNEL_ID, sha=_SHA, tier="T0")
        # Strip frontmatter (lines between --- and ---)
        lines = content.split("\n")
        # Find end of frontmatter
        fm_end = 0
        in_fm = False
        for i, line in enumerate(lines):
            if i == 0 and line == "---":
                in_fm = True
                continue
            if in_fm and line == "---":
                fm_end = i
                break
        body = "\n".join(lines[fm_end + 1 :]).strip()
        assert len(body) <= SNAPSHOT_T0_BODY_MAX_CHARS, (
            f"T0 body exceeds {SNAPSHOT_T0_BODY_MAX_CHARS} chars: {len(body)} chars"
        )

    def test_t0_frontmatter_contains_tier(self) -> None:
        content = render_snapshot(channel_id=_CHANNEL_ID, sha=_SHA, tier="T0")
        assert "Tier: T0" in content

    def test_t0_sha_truncated_in_frontmatter(self) -> None:
        content = render_snapshot(channel_id=_CHANNEL_ID, sha=_SHA, tier="T0")
        assert _SHA[:8] in content


class TestT1Render:
    def test_t1_within_600_chars(self) -> None:
        """FR13: T1 total ≤ 600 chars."""
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR
        )
        # Count body chars (after frontmatter)
        lines = content.split("\n")
        fm_end = 0
        for i, line in enumerate(lines):
            if i == 0:
                continue
            if line == "---":
                fm_end = i
                break
        body = "\n".join(lines[fm_end + 1 :])
        assert len(body) <= SNAPSHOT_T1_MAX_CHARS, (
            f"T1 body exceeds {SNAPSHOT_T1_MAX_CHARS} chars: {len(body)}"
        )

    def test_t1_includes_top_risk_file(self) -> None:
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR
        )
        assert "src/module_a.py" in content

    def test_t1_includes_convention(self) -> None:
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T1", sidecar=_SAMPLE_SIDECAR
        )
        assert "structlog" in content or "CONVENTION" in content

    def test_t1_without_sidecar_has_fallback(self) -> None:
        content = render_snapshot(channel_id=_CHANNEL_ID, sha=_SHA, tier="T1", sidecar=None)
        assert "No sidecar" in content or "trw-distill" in content


class TestT2Render:
    def test_t2_within_quota(self) -> None:
        """FR12: T2 ≤ 8192 bytes."""
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        assert len(content.encode("utf-8")) <= SNAPSHOT_QUOTA_BYTES

    def test_t2_includes_required_sections(self) -> None:
        """FR12: T2 must include top-5 risk files table, conventions, caution dirs."""
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        assert "Top Risk Files" in content or "risk_files" in content or "module_a" in content
        assert "Convention" in content or "structlog" in content
        assert "Caution" in content or "state" in content

    def test_t2_includes_top5_risk_files(self) -> None:
        content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        # All 5 risk files should appear
        for i in range(1, 6):
            assert f"module_{chr(ord('a') + i - 1)}.py" in content

    def test_t3_same_content_as_t2(self) -> None:
        """T3 is maximum tier; content is same structure as T2."""
        t2_content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        t3_content = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T3", sidecar=_SAMPLE_SIDECAR
        )
        # Both should have the same structural elements
        assert ("Top Risk Files" in t2_content) == ("Top Risk Files" in t3_content)

    def test_render_idempotent_same_data(self) -> None:
        """NFR09: same sidecar + tier → byte-identical output (timestamps are day-truncated)."""
        content1 = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        content2 = render_snapshot(
            channel_id=_CHANNEL_ID, sha=_SHA, tier="T2", sidecar=_SAMPLE_SIDECAR
        )
        assert content1 == content2
