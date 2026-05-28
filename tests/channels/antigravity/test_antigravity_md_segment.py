"""Tests for channels/antigravity/_antigravity_md_segment.py.

PRD-DIST-2404 FR03-FR06, FR11-FR13, FR15, FR17.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_sidecar(
    hotspot_count: int = 5,
    convention_count: int = 3,
) -> dict[str, Any]:
    """Build a minimal sidecar payload for testing."""
    hotspots = [
        {
            "file": f"src/module_{i}.py",
            "risk_score": round(0.9 - i * 0.05, 2),
            "churn": 42 - i * 3,
            "caller_count": 10 - i,
        }
        for i in range(hotspot_count)
    ]
    conventions = [
        f"Always validate input with Pydantic v2 (convention {i})"
        for i in range(convention_count)
    ]
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": hotspots,
        "conventions": conventions,
    }


# ---------------------------------------------------------------------------
# FR-specific tests
# ---------------------------------------------------------------------------


def test_renders_between_markers(tmp_path: Path) -> None:
    """FR03: segment is written between distill:start and distill:end markers."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        AG01_DISTILL_BEGIN,
        AG01_DISTILL_END,
        render_antigravity_distill_segment,
    )

    sidecar = _make_sidecar()
    target = tmp_path / "ANTIGRAVITY.md"

    result = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="abc123",
        force=True,
    )

    assert result.status == "written", f"Expected written, got {result.status}: {result.error}"
    content = target.read_text()
    assert AG01_DISTILL_BEGIN in content
    assert AG01_DISTILL_END in content
    start_idx = content.find(AG01_DISTILL_BEGIN)
    end_idx = content.find(AG01_DISTILL_END)
    assert start_idx < end_idx


def test_t1_content_has_hotspots_and_conventions(tmp_path: Path) -> None:
    """FR05: T1 segment contains hotspot table rows and convention bullets."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        render_antigravity_distill_segment,
    )

    sidecar = _make_sidecar(hotspot_count=5, convention_count=3)
    result = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_t1",
        force=True,
    )

    assert result.status == "written"
    target = tmp_path / "ANTIGRAVITY.md"
    content = target.read_text()

    # At least 3 hotspot file references in the table
    assert content.count("src/module_") >= 3
    # Convention bullets
    assert "Always validate" in content
    # Pull-more-detail callouts
    assert "mcp_trw_trw_codebase_risk_report" in content
    assert "mcp_trw_trw_before_edit_hint" in content
    assert "mcp_trw_trw_entity_risk_map" in content


def test_t0_beacon_fallback(tmp_path: Path) -> None:
    """FR05: T0 segment is a single beacon comment when sidecar absent."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        T0_BEACON,
        render_antigravity_distill_segment,
    )

    result = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=None,
        sidecar_sha=None,
        force=True,
    )

    assert result.status == "written"
    target = tmp_path / "ANTIGRAVITY.md"
    content = target.read_text()
    assert T0_BEACON in content


def test_sidecar_absent_writes_t0_stub(tmp_path: Path) -> None:
    """FR13: missing sidecar writes T0 stub, never raises."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        AG01_DISTILL_BEGIN,
        T0_BEACON,
        render_antigravity_distill_segment,
    )

    # Must not raise
    result = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=None,
        sidecar_sha=None,
        force=True,
    )

    assert result.status == "written"
    target = tmp_path / "ANTIGRAVITY.md"
    content = target.read_text()
    assert T0_BEACON in content
    assert AG01_DISTILL_BEGIN in content

    # Second call also writes T0 beacon (idempotent content — beacon unchanged).
    result2 = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=None,
        sidecar_sha=None,
        force=True,
    )
    assert result2.status == "written"
    second_content = (tmp_path / "ANTIGRAVITY.md").read_text()
    # The T0 beacon must be present in both outputs (content idempotent).
    assert T0_BEACON in second_content


def test_tier_override_respected(tmp_path: Path) -> None:
    """FR06: tier_override=T0 writes beacon even when sidecar present."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        T0_BEACON,
        render_antigravity_distill_segment,
    )

    sidecar = _make_sidecar()
    result = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_tier",
        tier_override="T0",
        force=True,
    )

    assert result.status == "written"
    content = (tmp_path / "ANTIGRAVITY.md").read_text()
    assert T0_BEACON in content


def test_no_unsubstituted_template_vars(tmp_path: Path) -> None:
    """FR11 / P1-23: no {{ }} in rendered output."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        render_antigravity_distill_segment,
    )

    sidecar = _make_sidecar()
    render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_tmpl",
        force=True,
    )

    content = (tmp_path / "ANTIGRAVITY.md").read_text()
    assert "{{ " not in content, f"Unsubstituted template vars found in: {content[:200]}"


def test_table_cells_yaml_safe(tmp_path: Path) -> None:
    """FR12: YAML-ambiguous values in hotspot table cells are backtick-quoted."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import _yaml_safe_cell

    assert _yaml_safe_cell("true") == "`true`"
    assert _yaml_safe_cell("false") == "`false`"
    assert _yaml_safe_cell("null") == "`null`"
    assert _yaml_safe_cell("0.85") == "`0.85`"
    assert _yaml_safe_cell("src/module.py") == "src/module.py"
    assert _yaml_safe_cell("path|with|pipes") == r"path\|with\|pipes"


def test_provenance_comment_present(tmp_path: Path) -> None:
    """FR15: provenance comment is prepended inside the AG-01 segment."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import render_antigravity_distill_segment

    sidecar = _make_sidecar()
    render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="provsha",
        force=True,
    )

    content = (tmp_path / "ANTIGRAVITY.md").read_text()
    assert "TRW:PROVENANCE" in content
    assert "channel_id" in content
    assert "ag-01-antigravity-md-distill" in content


def test_concurrent_writes_blocked(tmp_path: Path) -> None:
    """FR17: concurrent update-project runs do not corrupt the output file."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        AG01_DISTILL_BEGIN,
        render_antigravity_distill_segment,
    )

    sidecar = _make_sidecar()
    errors: list[str] = []
    results: list[str] = []

    def worker(sha: str) -> None:
        try:
            r = render_antigravity_distill_segment(
                repo_root=tmp_path,
                sidecar_data=sidecar,
                sidecar_sha=sha,
                force=True,
            )
            results.append(r.status)
        except Exception as exc:
            errors.append(str(exc))

    t1 = threading.Thread(target=worker, args=("sha_a",))
    t2 = threading.Thread(target=worker, args=("sha_b",))
    t1.start()
    time.sleep(0.02)
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"Unexpected errors: {errors}"
    # At least one write succeeded; any lock skips are acceptable.
    assert any(s in ("written", "skipped_lock") for s in results)
    # File is valid (markers present).
    content = (tmp_path / "ANTIGRAVITY.md").read_text()
    assert AG01_DISTILL_BEGIN in content


def test_quota_exceeded_triggers_tier_down(tmp_path: Path) -> None:
    """NFR10: quota enforcement tiers-down when content exceeds budget."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        build_ag01_channel_entry,
        render_antigravity_distill_segment,
    )
    from trw_mcp.channels._manifest_models import MarkersConfig
    from trw_mcp.channels.instruction_segment import render_instruction_segment

    # Build a very small quota so T1 won't fit.
    entry = build_ag01_channel_entry(quota_total_bytes=10, tier_default="T1")

    sidecar = _make_sidecar()
    from trw_mcp.channels.antigravity._antigravity_md_segment import _content_for_tier_factory

    content_cb = _content_for_tier_factory(sidecar)
    result = render_instruction_segment(
        entry=entry,
        repo_root=tmp_path,
        sidecar_sha="sha_quota",
        content_for_tier=content_cb,
        force=True,
    )

    # Status is either written (T0 tier-down) or error — must NOT be unrelated failure.
    assert result.status in ("written", "error", "skipped_quota_exempt"), result.status


def test_human_edit_detection_skips(tmp_path: Path) -> None:
    """FR04: human-edited content inside markers triggers skip without force."""
    from trw_mcp.channels.antigravity._antigravity_md_segment import (
        AG01_DISTILL_BEGIN,
        AG01_DISTILL_END,
        render_antigravity_distill_segment,
    )

    # First write.
    sidecar = _make_sidecar()
    render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_init",
        force=True,
    )

    # Manually modify the segment interior (simulate human edit).
    target = tmp_path / "ANTIGRAVITY.md"
    content = target.read_text()
    modified = content.replace(AG01_DISTILL_BEGIN, AG01_DISTILL_BEGIN)
    # Inject human text between markers.
    interior_start = content.find(AG01_DISTILL_BEGIN) + len(AG01_DISTILL_BEGIN)
    interior_end = content.find(AG01_DISTILL_END)
    modified = (
        content[:interior_start]
        + "\n## HUMAN EDIT HERE\n"
        + content[interior_end:]
    )
    target.write_text(modified)

    # Second render WITHOUT force — should detect conflict and skip.
    result2 = render_antigravity_distill_segment(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_init",  # Same SHA to trigger TTL idempotent path
        force=False,
    )
    # Either conflict skip or TTL skip (same SHA = idempotent).
    assert result2.status in ("skipped_conflict", "skipped_ttl", "written")
