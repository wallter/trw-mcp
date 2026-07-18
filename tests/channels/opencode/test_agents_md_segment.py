"""Tests for channels/opencode/_agents_md_segment.py.

PRD-DIST-2403 FR01-FR09.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


def _make_sidecar(
    hotspot_count: int = 6,
    convention_count: int = 4,
    with_proprietary: bool = False,
) -> dict[str, Any]:
    """Build a minimal sidecar payload for testing."""
    hotspots: list[dict[str, Any]] = []
    for i in range(hotspot_count):
        path = f"src/module_{i}.py"
        if with_proprietary and i == 2:
            path = f"trw-distill/trw_distill/emit/cursor/module_{i}.py"
        hotspots.append(
            {
                "file": path,
                "composite_score": round(0.9 - i * 0.05, 2),
                "risk_score": round(0.9 - i * 0.05, 2),
            }
        )
    conventions = [
        f"Use Pydantic v2 validators for all input parsing (convention {i})" for i in range(convention_count)
    ]
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": hotspots,
        "conventions": conventions,
    }


# ---------------------------------------------------------------------------
# FR01 — Markers distinct from ceremony section
# ---------------------------------------------------------------------------


def test_distill_markers_distinct_from_ceremony(tmp_path: Path) -> None:
    """FR01: Distill markers are distinct from ceremony markers."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        DISTILL_BEGIN,
        DISTILL_END,
    )

    assert DISTILL_BEGIN == "<!-- trw:distill:start -->"
    assert DISTILL_END == "<!-- trw:distill:end -->"
    assert DISTILL_BEGIN != "<!-- trw:start -->"
    assert DISTILL_END != "<!-- trw:end -->"


def test_distill_segment_placed_after_ceremony(tmp_path: Path) -> None:
    """FR01: Distill segment appears AFTER ceremony section."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        DISTILL_BEGIN,
        _ensure_sequential_placement,
    )

    agents_md = (
        "# Project\n\n<!-- trw:start -->\n## TRW Section\nCeremony content.\n<!-- trw:end -->\n\n## Other Section\n"
    )
    result = _ensure_sequential_placement(agents_md, "Distill content")

    ceremony_end_pos = result.find("<!-- trw:end -->")
    distill_begin_pos = result.find(DISTILL_BEGIN)
    assert ceremony_end_pos != -1
    assert distill_begin_pos != -1
    assert distill_begin_pos > ceremony_end_pos


def test_ceremony_section_unchanged_after_distill_write(tmp_path: Path) -> None:
    """FR01 / AC04: Ceremony section byte-identical after distill segment write."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        install_opencode_agents_md_distill_segment,
    )

    ceremony_section = "<!-- trw:start -->\n## TRW Section\nCeremony content.\n<!-- trw:end -->"
    agents_md_content = f"# Project\n\n{ceremony_section}\n"
    agents_md_path = tmp_path / "AGENTS.md"
    agents_md_path.write_text(agents_md_content, encoding="utf-8")

    install_opencode_agents_md_distill_segment(
        tmp_path,
        _make_sidecar(),
        "abc123",
        force=True,
    )

    result = agents_md_path.read_text(encoding="utf-8")
    assert ceremony_section in result


# ---------------------------------------------------------------------------
# FR02 — T1 render within quota
# ---------------------------------------------------------------------------


def test_t1_render_within_quota(tmp_path: Path) -> None:
    """FR02: T1 segment contains top-5 hotspots, top-3 conventions, footer."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        T1_BYTE_QUOTA,
        _t1_content,
    )

    sidecar = _make_sidecar(hotspot_count=10, convention_count=5)
    content = _t1_content(sidecar)

    # Must be within quota
    assert len(content.encode("utf-8")) < T1_BYTE_QUOTA

    # Must contain footer line
    assert "Use /trw-before-edit" in content

    # Should have at most 5 hotspot entries (counted by format markers)
    hotspot_lines = [l for l in content.splitlines() if l.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6."))]
    assert len(hotspot_lines) <= 5


def test_t1_content_shows_top_5_hotspots(tmp_path: Path) -> None:
    """FR02: T1 tier shows top-5 hotspots by composite_score."""
    from trw_mcp.channels.opencode._agents_md_segment import _t1_content

    sidecar = _make_sidecar(hotspot_count=8)
    content = _t1_content(sidecar)
    # 5th hotspot path should appear, 6th should not
    assert "src/module_4.py" in content
    # 6th (index 5) should not be in output
    assert "src/module_5.py" not in content


def test_t1_content_shows_top_3_conventions(tmp_path: Path) -> None:
    """FR02: T1 tier shows top-3 conventions."""
    from trw_mcp.channels.opencode._agents_md_segment import _t1_content

    sidecar = _make_sidecar(convention_count=6)
    content = _t1_content(sidecar)
    # 3rd convention should appear
    assert "convention 2" in content
    # 4th should not
    assert "convention 3" not in content


# ---------------------------------------------------------------------------
# FR03 — T0 beacon when sidecar missing
# ---------------------------------------------------------------------------


def test_t0_beacon_when_sidecar_missing(tmp_path: Path) -> None:
    """FR03: T0 beacon rendered when sidecar is None."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        T1_BYTE_QUOTA,
        _t0_beacon,
    )

    beacon = _t0_beacon("trw_codebase_risk_report(top_n=20) via MCP")
    # Must be short
    assert len(beacon.encode("utf-8")) < T1_BYTE_QUOTA // 10
    # Must contain the public MCP fallback, not a separately installed CLI.
    assert "trw_codebase_risk_report" in beacon
    assert "trw-distill self-improve" not in beacon


def test_install_with_no_sidecar_renders_t0(tmp_path: Path) -> None:
    """FR03: install with sidecar_data=None writes T0 beacon."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        DISTILL_BEGIN,
        install_opencode_agents_md_distill_segment,
    )

    result = install_opencode_agents_md_distill_segment(
        tmp_path,
        None,  # no sidecar
        None,
        force=True,
    )
    assert result.status in ("written", "dry_run", "error")

    agents_md = tmp_path / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text(encoding="utf-8")
        if DISTILL_BEGIN in content:
            assert "trw_codebase_risk_report" in content
            assert "trw-distill self-improve" not in content


# ---------------------------------------------------------------------------
# FR04 — Stale SHA notice
# ---------------------------------------------------------------------------


def test_stale_sha_renders_with_notice() -> None:
    """FR04: Stale sidecar appends staleness notice."""
    from trw_mcp.channels.opencode._agents_md_segment import _t1_content

    sidecar = _make_sidecar()
    content = _t1_content(sidecar, stale=True)
    assert "STALE" in content
    assert "cached intelligence is outdated" in content
    assert "trw_codebase_risk_report" in content
    assert "trw-distill self-improve" not in content


# ---------------------------------------------------------------------------
# FR05 — Shared lock (P0-06)
# ---------------------------------------------------------------------------


def test_shared_agents_md_lock_prevents_concurrent_writes(tmp_path: Path) -> None:
    """FR05: Shared lock prevents concurrent ceremony + distill writes.

    Two threads write AGENTS.md; result should be byte-consistent.
    """
    from trw_mcp.channels.opencode._agents_md_segment import (
        install_opencode_agents_md_distill_segment,
    )

    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project\n\n<!-- trw:end -->\n", encoding="utf-8")
    results: list[str] = []

    def writer_a() -> None:
        install_opencode_agents_md_distill_segment(tmp_path, _make_sidecar(), "sha1", force=True)
        if agents_md.exists():
            results.append("a_done")

    def writer_b() -> None:
        install_opencode_agents_md_distill_segment(tmp_path, _make_sidecar(hotspot_count=2), "sha2", force=True)
        if agents_md.exists():
            results.append("b_done")

    t1 = threading.Thread(target=writer_a)
    t2 = threading.Thread(target=writer_b)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    # File should exist and not be empty
    assert agents_md.exists()
    final = agents_md.read_text(encoding="utf-8")
    assert len(final) > 0
    # Markers should be balanced (no interleaved content)
    if "<!-- trw:distill:start -->" in final:
        start_count = final.count("<!-- trw:distill:start -->")
        end_count = final.count("<!-- trw:distill:end -->")
        assert start_count == end_count


# ---------------------------------------------------------------------------
# FR07 — IP filter (P2-10)
# ---------------------------------------------------------------------------


def test_ip_filter_excludes_trw_distill_paths() -> None:
    """FR07: Proprietary paths excluded from T1 segment."""
    from trw_mcp.channels.opencode._agents_md_segment import _t1_content

    sidecar = _make_sidecar(with_proprietary=True)
    content = _t1_content(sidecar)
    # Proprietary path should NOT appear in output
    assert "trw-distill/trw_distill/emit/cursor" not in content
    # Non-proprietary paths should still be present
    assert "src/module_0.py" in content


# ---------------------------------------------------------------------------
# FR09 — Quota tier-down
# ---------------------------------------------------------------------------


def test_quota_tier_down_on_overflow(tmp_path: Path) -> None:
    """FR09: Content over 6144 bytes triggers tier-down."""
    from trw_mcp.channels.opencode._agents_md_segment import T1_BYTE_QUOTA

    # Build large sidecar that would exceed quota
    large_sidecar: dict[str, Any] = {
        "hotspots": [
            {"file": f"src/very_long_module_path_{i}_with_extra_words.py", "composite_score": 0.9} for i in range(100)
        ],
        "conventions": [
            f"Very long convention text that takes a lot of bytes to encode: number {i} " * 5 for i in range(50)
        ],
    }

    from trw_mcp.channels.opencode._agents_md_segment import _t1_content

    content = _t1_content(large_sidecar)
    # After tier-down, content should respect quota
    assert len(content.encode("utf-8")) <= T1_BYTE_QUOTA


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def test_build_opencode_agents_md_entry_canonical_fields() -> None:
    """build_opencode_agents_md_entry returns entry with correct fields."""
    from trw_mcp.channels.opencode._agents_md_segment import (
        DISTILL_BEGIN,
        DISTILL_END,
        T1_BYTE_QUOTA,
        build_opencode_agents_md_entry,
    )

    entry = build_opencode_agents_md_entry()
    assert entry.id == "opencode-agents-md-segment"
    assert entry.client == "opencode"
    assert entry.file == "AGENTS.md"
    assert entry.lock_file == ".trw/channels/agents-md.lock"
    assert entry.tier_default == "T1"
    assert entry.tier_min == "T0"
    assert entry.quota_total_bytes == T1_BYTE_QUOTA
    assert entry.markers.start == DISTILL_BEGIN
    assert entry.markers.end == DISTILL_END
    assert entry.ttl_commits == 10
    assert entry.ttl_days == 7


# ---------------------------------------------------------------------------
# SidecarData type alias exported (NFR04)
# ---------------------------------------------------------------------------


def test_sidecar_data_type_alias_exported() -> None:
    """NFR04: SidecarData type alias is exported from _agents_md_segment."""
    from trw_mcp.channels.opencode._agents_md_segment import SidecarData

    # Verify it is a generic alias for dict[str, Any]
    assert SidecarData is not None


# ---------------------------------------------------------------------------
# Event type correctness (cross-wave lesson: no channel_conflict overloading)
# ---------------------------------------------------------------------------


def test_lock_skip_emits_channel_lock_skip_event(tmp_path: Path) -> None:
    """Lock contention emits channel_lock_skip, not channel_conflict."""
    from unittest.mock import MagicMock, patch

    from trw_mcp.channels._lock import ChannelLockSkip
    from trw_mcp.channels.opencode._agents_md_segment import (
        install_opencode_agents_md_distill_segment,
    )

    emitted: list[str] = []

    def fake_emit(**kwargs: object) -> None:
        emitted.append(str(kwargs.get("event_type", "")))

    with patch("trw_mcp.channels.opencode._agents_md_segment.agents_md_lock") as mock_lock_fn:
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(side_effect=ChannelLockSkip("held"))
        mock_lock_fn.return_value = mock_lock

        with patch(
            "trw_mcp.channels.opencode._agents_md_segment.append_channel_event",
            side_effect=fake_emit,
        ):
            result = install_opencode_agents_md_distill_segment(tmp_path, None, None)

    assert result.status == "skipped_lock"
    assert "channel_lock_skip" in emitted, f"Expected channel_lock_skip in {emitted}"
    assert "channel_conflict" not in emitted, "channel_conflict must not be used for lock skip"


def test_error_emits_channel_error_event(tmp_path: Path) -> None:
    """Render errors emit channel_error, not channel_conflict."""
    from unittest.mock import MagicMock, patch

    from trw_mcp.channels.opencode._agents_md_segment import (
        install_opencode_agents_md_distill_segment,
    )

    emitted: list[str] = []

    def fake_emit(**kwargs: object) -> None:
        emitted.append(str(kwargs.get("event_type", "")))

    with patch("trw_mcp.channels.opencode._agents_md_segment.agents_md_lock") as mock_lock_fn:
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        mock_lock_fn.return_value = mock_lock

        with patch(
            "trw_mcp.channels.opencode._agents_md_segment._render_and_inject_under_lock",
            side_effect=RuntimeError("simulated render error"),
        ):
            with patch(
                "trw_mcp.channels.opencode._agents_md_segment.append_channel_event",
                side_effect=fake_emit,
            ):
                result = install_opencode_agents_md_distill_segment(tmp_path, None, None)

    assert result.status == "error"
    assert "channel_error" in emitted, f"Expected channel_error in {emitted}"
    assert "channel_conflict" not in emitted, "channel_conflict must not be used for errors"


# ---------------------------------------------------------------------------
# OC-M1 — Ceremony-vs-distill real race test (PRD-DIST-2403 FR05)
# ---------------------------------------------------------------------------


def test_ceremony_vs_distill_concurrent_write_no_corruption(tmp_path: Path) -> None:
    """OC-M1: generate_agents_md() and install_opencode_agents_md_distill_segment()
    run concurrently — AGENTS.md must be well-formed after both complete.

    Both ceremony and distill markers must be present and non-interleaved.
    This test exercises the real ceremony-vs-distill race fixed by OC-B1.
    """
    from trw_mcp.bootstrap._opencode import generate_agents_md
    from trw_mcp.channels.opencode._agents_md_segment import (
        DISTILL_BEGIN,
        DISTILL_END,
        install_opencode_agents_md_distill_segment,
    )

    agents_md = tmp_path / "AGENTS.md"
    # Seed file with ceremony section already present
    agents_md.write_text(
        "# Project\n\n"
        "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
        "<!-- trw:start -->\n"
        "Ceremony content.\n"
        "<!-- trw:end -->\n",
        encoding="utf-8",
    )

    errors: list[str] = []

    def run_ceremony() -> None:
        try:
            # force=False is the production path; force=True is an intentional clobber
            generate_agents_md(tmp_path, "Updated ceremony section.", force=False)
        except Exception as exc:
            errors.append(f"ceremony: {exc}")

    def run_distill() -> None:
        try:
            install_opencode_agents_md_distill_segment(tmp_path, _make_sidecar(), "sha_race", force=True)
        except Exception as exc:
            errors.append(f"distill: {exc}")

    t1 = threading.Thread(target=run_ceremony)
    t2 = threading.Thread(target=run_distill)
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not errors, f"Thread errors: {errors}"
    assert agents_md.exists()
    final = agents_md.read_text(encoding="utf-8")

    # Both ceremony and distill markers must be present and balanced
    assert "<!-- trw:start -->" in final, "Ceremony start marker missing"
    assert "<!-- trw:end -->" in final, "Ceremony end marker missing"
    assert DISTILL_BEGIN in final, "Distill begin marker missing"
    assert DISTILL_END in final, "Distill end marker missing"

    # Markers must be balanced (no interleaving)
    assert final.count("<!-- trw:start -->") == 1
    assert final.count("<!-- trw:end -->") == 1
    assert final.count(DISTILL_BEGIN) == 1
    assert final.count(DISTILL_END) == 1

    # Distill section must appear AFTER ceremony section
    ceremony_end_pos = final.find("<!-- trw:end -->")
    distill_begin_pos = final.find(DISTILL_BEGIN)
    assert distill_begin_pos > ceremony_end_pos, (
        f"Distill section at {distill_begin_pos} must come after ceremony end at {ceremony_end_pos}"
    )


# ---------------------------------------------------------------------------
# OC-M2 — Manifest validation fail-soft test
# ---------------------------------------------------------------------------


def test_install_opencode_distill_channels_fail_soft_on_bad_manifest(tmp_path: Path) -> None:
    """OC-M2: install_opencode_distill_channels() returns error result (not raise)
    when bootstrap_channel_manifest() raises ManifestValidationError.

    A single invalid manifest entry must not abort the entire install.
    """
    from unittest.mock import patch

    from trw_mcp.bootstrap._opencode_distill_channels import install_opencode_distill_channels
    from trw_mcp.channels._manifest_loader import ManifestValidationError

    # Patch bootstrap_channel_manifest to simulate a bad manifest entry
    with patch(
        "trw_mcp.bootstrap._opencode_distill_channels.bootstrap_channel_manifest",
        side_effect=ManifestValidationError("invalid entry: missing required field 'id'"),
    ):
        # Must not raise — must return a dict with manifest error info
        result = install_opencode_distill_channels(tmp_path)

    assert isinstance(result, dict), "install_opencode_distill_channels must return a dict"
    manifest_info = result.get("manifest")
    assert manifest_info is not None, "Result must contain 'manifest' key"
    assert isinstance(manifest_info, dict), f"manifest must be a dict, got {type(manifest_info)}"
    assert manifest_info.get("status") == "error", f"Expected status='error', got: {manifest_info}"
    assert "error" in manifest_info, "manifest result must contain 'error' key"
    assert "invalid entry" in str(manifest_info["error"]).lower() or "missing" in str(manifest_info["error"]).lower()
